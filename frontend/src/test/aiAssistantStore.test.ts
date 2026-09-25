import { beforeEach, describe, expect, it, vi } from 'vitest'
import { postAIChat, streamAIChat } from '@/shared/api/endpoints'
import { useAIAssistantStore } from '@/state/aiAssistantStore'

vi.mock('@/shared/api/endpoints', () => ({
  postAIChat: vi.fn(),
  streamAIChat: vi.fn(),
}))

describe('aiAssistantStore', () => {
  beforeEach(() => {
    useAIAssistantStore.setState({
      open: false,
      loading: false,
      streaming: false,
      sessionId: null,
      messages: [],
      pageContext: { page: 'generic', title: 'Final Trade' },
    })
    vi.mocked(streamAIChat).mockReset()
    vi.mocked(postAIChat).mockReset()
  })

  it('clears loading state when session is cleared', () => {
    useAIAssistantStore.setState({ loading: true, streaming: true })
    useAIAssistantStore.getState().clearSession()
    const state = useAIAssistantStore.getState()
    expect(state.loading).toBe(false)
    expect(state.streaming).toBe(false)
    expect(state.messages).toEqual([])
  })

  it('falls back to non-stream chat when stream does not finish', async () => {
    vi.mocked(streamAIChat).mockImplementation(async (_payload, handlers) => {
      handlers.onError?.('流式响应未完成')
    })
    vi.mocked(postAIChat).mockResolvedValue({
      session_id: 'sess-1',
      message: { role: 'assistant', content: 'fallback reply' },
    })

    await useAIAssistantStore.getState().sendMessage('hello')

    const state = useAIAssistantStore.getState()
    expect(postAIChat).toHaveBeenCalledTimes(1)
    expect(state.loading).toBe(false)
    expect(state.messages.at(-1)).toEqual({ role: 'assistant', content: 'fallback reply' })
  })

  it('blocks concurrent sends while loading', async () => {
    vi.mocked(streamAIChat).mockImplementation(
      () => new Promise(() => {
        // never resolves
      }),
    )

    void useAIAssistantStore.getState().sendMessage('first')
    await useAIAssistantStore.getState().sendMessage('second')

    expect(streamAIChat).toHaveBeenCalledTimes(1)
    expect(useAIAssistantStore.getState().messages.filter((item) => item.role === 'user')).toHaveLength(1)
  })
})
