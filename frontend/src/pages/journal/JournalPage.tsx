import { Typography } from 'antd'

export function JournalPage() {
  return (
    <section aria-label="实盘复盘工作台">
      <Typography.Title level={3} style={{ marginTop: 0 }}>实盘复盘</Typography.Title>
      <Typography.Paragraph type="secondary">
        交易记录、资金账本、每日与周期复盘集中在这里。数据保存在本地。
      </Typography.Paragraph>
      <iframe
        title="波段复盘志"
        src="/journal-app/"
        style={{ width: '100%', minHeight: 'calc(100vh - 190px)', border: '1px solid #d9e4de', borderRadius: 12, background: '#fff' }}
      />
    </section>
  )
}
