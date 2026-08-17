"""消息优先级队列与打断语义（增强机制 §2.3）。

- message_queue：两级优先级（next 控制类 > later 普通追问），同级 FIFO，
  上限 20 条超出拒绝；入队/出队推 message.queued / message.dequeued 事件；
- inbox：运行中消息收件箱（deps 注入；InMemory / Redis 实现），Loop 在
  流式输出与工具执行期间的决策点非阻塞 poll。
"""
