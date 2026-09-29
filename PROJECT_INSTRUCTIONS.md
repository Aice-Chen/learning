# 网页端 Project 指令模板

在 claude.ai 和 ChatGPT 中各新建一个用于学习的 Project，启用 learning 连接器，把下面这段话填进 Project 指令：

```
这个项目用于我的个人学习。每次学习会话开始时，先调用 learning 的 start_session 工具（include_guide 设为 true），并按返回的 AGENTS.md 与我协作。
```

AGENTS.md 在网页端是作为工具结果出现在对话中的，而模型对工具结果中的指令会比较谨慎。这句话由你写在 Project 指令里，常驻在上下文中，让模型把 AGENTS.md 当作你的要求来执行。
