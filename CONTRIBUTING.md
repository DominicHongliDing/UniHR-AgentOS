# Contributing

欢迎通过 Issue 或 Pull Request 改进 UniHR AgentOS。

## 本地验证

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm run build
```

## 贡献边界

- 不要提交真实政策、简历、身份证号、薪酬表或聊天记录；
- 不要在测试中使用真实人员姓名和组织名称；
- 新增能力优先注册到 Capability Registry，避免继续扩大总路由关键词表；
- 新增 Skill 必须包含版本、输入输出约束、Dry Run 和失败行为；
- 生成文档或表格的功能应补充 Artifact Harness 验证。

