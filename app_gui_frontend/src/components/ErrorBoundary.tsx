import React from 'react'

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: string | null }> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: any) {
    return { error: error?.message || String(error) }
  }

  componentDidCatch(error: any, info: any) {
    console.error('R-Agent Cockpit render error:', error, info)
  }

  render() {
    if (this.state.error) {
      return <div className="cockpit-shell">
        <div className="glass-panel" style={{ padding: 24 }}>
          <div className="panel-title">R-Agent Cockpit 渲染错误</div>
          <pre className="full-payload">{this.state.error}</pre>
          <button className="primary wide" onClick={() => location.reload()}>刷新界面</button>
        </div>
      </div>
    }
    return this.props.children
  }
}
