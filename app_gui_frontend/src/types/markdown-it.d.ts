declare module 'markdown-it' {
  type MarkdownItOptions = Record<string, unknown>

  class MarkdownIt {
    constructor(options?: MarkdownItOptions)
    render(src: string, env?: unknown): string
    utils: {
      escapeHtml(src: string): string
      [key: string]: unknown
    }
  }

  export default MarkdownIt
}
