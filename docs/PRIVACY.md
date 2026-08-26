# Privacy boundary

UniHR AgentOS 的代码可以公开，组织数据不能默认公开。

## 永不提交到 Git

- `.env` 及任何 API Key、App Secret、Token、Cookie；
- `backend/data/` 下的数据库、政策原文、向量索引、简历、附件和产物；
- 日志、浏览器配置、Office 临时文件和本机绝对路径；
- 从真实用户反馈生成但尚未脱敏的评测样本。

## 推荐发布前检查

1. 确认 `git status --ignored` 中所有运行数据均处于 ignored；
2. 对待提交文件运行密钥扫描；
3. 在 GitHub 公共仓库启用 Secret Scanning 与 Push Protection；
4. 使用全新 API Key 做一次安装测试，不复用开发机密钥；
5. 若密钥曾进入 Git 历史，必须先撤销并重新签发，不能只删除当前文件。

## 数据责任

部署者负责确认其上传政策和人事材料的使用权限、保存期限、访问控制与适用法律。招聘输出只能辅助人工复核，不应基于受保护属性实施自动就业决策。

