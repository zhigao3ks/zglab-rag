# Source Acquisition & Gitee Mirror Runtime

本维护轨道将“公开来源的身份”和“生产拉取通道”明确分离：

```text
canonical repository (GitHub)  → provenance / citation / public project URL
acquisition mirror (Gitee)     → production clone / fetch transport
```

`config/sources.yaml` 中 Git source 的 `repository` 永远是 canonical GitHub
slug，例如 `zhigao3ks/notes`。若生产需要镜像，显式增加：

```yaml
acquisition:
  provider: gitee
  repository: Zg443/notes
```

系统只会构造 `https://gitee.com/Zg443/notes.git`，不接受配置中的任意 Git URL。
因此 GitHub owner 与 Gitee owner 可以不同。最终 Markdown provenance 仍生成
`https://github.com/zhigao3ks/notes/blob/<revision>/<path>`；Gitee URL 不会进入
public citation contract。

## Checkout 生命周期

生产设置：

```text
ZGLAB_RAG_SOURCE_CHECKOUT_ROOT=/opt/zglab-rag/sources
```

每个 configured acquisition source 的 managed checkout 为 `<root>/<source-id>`。
`zglab-rag sources bootstrap` 与 `zglab-rag sync apply` 都只处理 registry 中
`enabled=true` 的 Git source：

1. 不存在时在同级临时目录 clone，验证 Git root、origin 与 revision 后原子 rename；
2. 已存在时验证 Git root、clean worktree 与 origin，随后 fetch 指定 `ref`，并仅执行
   `merge --ff-only`；
3. acquisition、fetch、merge、解析或 embedding 任一失败都会在 index apply 之前退出，
   当前 serving `knowledge.db` 不会被打开或替换。

临时 clone 失败会清理临时目录，避免下一轮把半成品识别为 source。dirty checkout、origin
mismatch、fetch failure 和 non-fast-forward 均 fail closed。

无 `acquisition` 的历史 Git source 继续使用相对项目根目录的 `local_path`，以便逐步迁移。
设置了 acquisition 的 source 始终优先使用 managed root，不会因旧 checkout 存在而读取它。
LOCAL source 不参与 Git bootstrap；disabled source 也不会 clone。公开 registry 仍是唯一授权
边界：HTTP API、用户输入、Agent 和 MCP 均不能新增来源或传入 URL。

## 生产迁移

不需要移动旧 checkout。部署后由管理员一次性创建并授权目录：

```bash
sudo install -d -o zglab -g zglab -m 0750 /opt/zglab-rag/sources
sudo -u zglab /opt/zglab-rag/app/.venv/bin/python -m zglab_rag.cli sources bootstrap
sudo -u zglab /opt/zglab-rag/app/.venv/bin/python -m zglab_rag.cli sync plan
```

确认 plan 后运行 `sync apply`。公开 Gitee mirror 不需要 credential；private mirror 应使用 Git
credential helper、受保护环境凭据或 SSH deploy key，绝不把 token、密码或 SSH key 放进 YAML、URL、
代码或 Git history。
