# @deepseek-ai/dsh-client-ui-education

TeachMate 的空会话欢迎区和显式上下文选择器。欢迎卡片只负责把建议问题填入输入框，选择器占用会话输入区，教师选择学期、考试和学生后，通过仅供宿主使用的 `/teachmate-context` 命令更新教育上下文。模型不能通过工具调用修改上下文，命令原始 ID 也不会写入会话日志。

## Model Experience

### 上下文选择

#### What the model sees

浏览器控件只改变宿主持有的教育作用域；模型只能看到更新后的教育工具状态，不会看到选择器中的原始表单字段。

#### Token effect

选择器本身不增加模型 token；选择成功后，后续教育工具读取新的上下文事实。

#### KV Cache effect

切换上下文不会重写历史轮次，只会改变后续工具调用使用的宿主作用域。

## Known Limitations and Deferred Work

- 当前版本接受数字 ID；后续将通过 WorkBench Remote 提供教师友好的可搜索名称选择。
