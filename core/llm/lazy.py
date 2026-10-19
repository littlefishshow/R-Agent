"""延迟加载包装（learning_note/L0.md 第 7 节）。

``lazy_stream`` 让"需要异步/惰性 setup 的 Provider"也满足统一契约：**同步返回**一个
``AssistantMessageEventStream``，setup 在后台线程进行。

* setup 成功：把内层 stream 的事件逐个转发给外层；
* setup 失败：不让后台异常变成无人处理的异常，而是产出一个 ``error`` 终止事件
  （error AssistantMessage），并结束外层流。

这保证调用方拿到 stream 后，只需用统一流协议观察完成/失败，不必再防守后台异常。
典型用途：延迟 import 某个厂商 adapter 模块、延迟刷新认证。
"""

from __future__ import annotations

import threading
from typing import Callable

from core.llm.event_stream import AssistantMessageEventStream
from core.llm.types import event_error, make_error_message


def lazy_stream(setup: Callable[[], AssistantMessageEventStream]) -> AssistantMessageEventStream:
    """立即返回外层 stream；后台执行 ``setup`` 得到内层 stream 并转发其事件。

    ``setup`` 是一个无参可调用对象，返回一个 ``AssistantMessageEventStream``。它可能
    抛异常（如模块缺失、认证失败），这些都会被转成外层的 ``error`` 终止事件。
    """

    outer = AssistantMessageEventStream()

    def _run() -> None:
        try:
            inner = setup()
        except Exception as exc:  # noqa: BLE001 - setup 失败转成统一 error 事件
            outer.push(event_error(make_error_message(str(exc))))
            return
        try:
            for event in inner:
                outer.push(event)
                if event.is_terminal:
                    break
            # 若内层既没发终止事件也结束了迭代，补一个 error，避免外层永久挂起。
            if not outer.is_done():
                outer.push(event_error(make_error_message("inner stream ended without terminal event")))
        except Exception as exc:  # noqa: BLE001
            if not outer.is_done():
                outer.push(event_error(make_error_message(str(exc))))

    threading.Thread(target=_run, name="r-agent-lazy-stream", daemon=True).start()
    return outer
