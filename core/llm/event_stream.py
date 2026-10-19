"""L0 事件流容器：连接「事件生产者（Provider adapter）」与「事件消费者」。

对齐 Pi 的 ``EventStream`` / ``AssistantMessageEventStream``（learning_note/L0.md
第 4 节）。与 Pi 的差异：R-Agent 主循环是**同步 + 线程**模型（``chat.completions``
同步调用、委派用线程），因此这里实现一个**线程安全的同步**事件流，而不是 async
迭代器。语义保持一致：

* 单消费者队列语义（single-consumer queue），**不是** publish/subscribe 广播；
* 两种观察视图共享同一次模型调用：
  - ``for event in stream`` 看完整过程（消费事件队列）；
  - ``stream.result()`` 只取最终 ``AssistantMessage``（不消费队列）；
* 终止事件（``done`` / ``error``）既作为过程最后一步交给迭代器，又用于解析
  ``result()``；
* 错误也是结构化终止（``error`` 事件 + error AssistantMessage），调用方不必混合
  处理事件与异常。

生产者典型用法（adapter）::

    stream = AssistantMessageEventStream()

    def _produce():
        try:
            stream.push(event_start())
            ...
            stream.push(event_done(reason, final_message))
        except Exception as exc:
            stream.push(event_error(make_error_message(str(exc))))

    threading.Thread(target=_produce, daemon=True).start()
    return stream

消费者典型用法（Agent Loop）::

    for event in stream:
        if event.type == "text_delta":
            render(event.delta)
    final = stream.result()   # 已终止，立即返回
"""

from __future__ import annotations

import threading
from typing import Iterator, List, Optional

from core.llm.types import AssistantMessage, AssistantMessageEvent, make_error_message


class FifoQueue:
    """均摊 O(1) 出队的 FIFO 队列（对齐 Pi 的 incoming/outgoing 双数组）。

    避免 ``list.pop(0)`` 每次搬动整个数组：入队压 ``incoming``；出队时若
    ``outgoing`` 空，则把 ``incoming`` 逆序搬过去再 ``pop()``。
    """

    def __init__(self) -> None:
        self._incoming: List[AssistantMessageEvent] = []
        self._outgoing: List[AssistantMessageEvent] = []

    def __len__(self) -> int:
        return len(self._incoming) + len(self._outgoing)

    def enqueue(self, item: AssistantMessageEvent) -> None:
        self._incoming.append(item)

    def dequeue(self) -> Optional[AssistantMessageEvent]:
        if not self._outgoing:
            if not self._incoming:
                return None
            self._incoming.reverse()
            self._outgoing = self._incoming
            self._incoming = []
        return self._outgoing.pop()


class AssistantMessageEventStream:
    """线程安全、单消费者的 ``AssistantMessageEvent`` 流。

    - ``push(event)``：生产者线程追加事件；终止事件会解析 ``result()``；
    - ``for event in stream``：消费者阻塞迭代，收完队列（含终止事件）后结束；
    - ``result(timeout)``：阻塞取最终 ``AssistantMessage``；
    - ``end(result)``：显式结束（主要用于包装/转发；若既不 push 终止事件也不传
      result，等待 ``result()`` 的代码不会得到最终值——与 Pi 的约束一致）。
    """

    def __init__(self) -> None:
        self._queue = FifoQueue()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._done = False            # 已经收到终止事件 / end()
        self._exhausted = False       # 迭代器是否已把终止事件交给消费者
        self._result: Optional[AssistantMessage] = None
        self._result_ready = threading.Event()

    # ------------------------------------------------------------------
    # 生产者侧
    # ------------------------------------------------------------------
    def push(self, event: AssistantMessageEvent) -> None:
        """追加一个事件。终止事件之后的 push 被忽略（Pi 第 4.5 节）。"""
        with self._cond:
            if self._done:
                return
            self._queue.enqueue(event)
            if event.is_terminal:
                self._done = True
                self._result = event.result_message() or make_error_message(
                    "stream terminated without a final message"
                )
                self._result_ready.set()
            self._cond.notify_all()

    def end(self, result: Optional[AssistantMessage] = None) -> None:
        """显式结束流。可选地提供最终 result（不另发终止事件）。"""
        with self._cond:
            if self._done:
                return
            self._done = True
            if result is not None:
                self._result = result
            self._result_ready.set()
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # 消费者侧：过程视图
    # ------------------------------------------------------------------
    def __iter__(self) -> Iterator[AssistantMessageEvent]:
        return self

    def __next__(self) -> AssistantMessageEvent:
        with self._cond:
            while True:
                event = self._queue.dequeue()
                if event is not None:
                    if event.is_terminal:
                        self._exhausted = True
                    return event
                if self._done:
                    # 队列已空且已终止：迭代结束。
                    self._exhausted = True
                    raise StopIteration
                self._cond.wait()

    # ------------------------------------------------------------------
    # 消费者侧：结果视图
    # ------------------------------------------------------------------
    def result(self, timeout: Optional[float] = None) -> AssistantMessage:
        """阻塞取最终 ``AssistantMessage``；不消费事件队列。

        若 ``end()`` 时既无终止事件也无 result，超时后返回一个 error 消息，避免调用方
        永久阻塞（防御 Pi 第 4.5 节提到的"无最终值"陷阱）。
        """
        if not self._result_ready.wait(timeout=timeout):
            return make_error_message("stream result timed out")
        return self._result or make_error_message("stream produced no final message")

    def is_done(self) -> bool:
        with self._lock:
            return self._done
