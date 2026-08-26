# Production User Management CLI

本文档记录 ZGLab RAG Phase 11 封板后的生产用户管理命令。

生产环境不开放注册；账号只能由管理员通过服务器 CLI 创建和管理。

## 1. 推荐调用方式

当前生产服务器历史 `.venv/bin/zglab-rag` console script 曾因跨机器复制 `.venv` 留有绝对 shebang，
因此运维统一使用 Python module invocation：

```bash
cd /opt/zglab-rag
sudo -u zglab env \
  PYTHONPATH=/opt/zglab-rag/app/src \
  /opt/zglab-rag/app/.venv/bin/python -m zglab_rag.cli <command>
```

必须从 `/opt/zglab-rag` 作为工作目录运行，因为生产 `.env` 位于：

```text
/opt/zglab-rag/.env
```

这样 Pydantic Settings 会读取正式生产配置，同时保留 JSON 类型环境值的原始格式；不要用 shell `source .env`
代替，因为类似 allowed-origins 的 JSON 值可能在 shell 解析中被破坏。

交互式 SSH 会话中可以先定义一个临时函数：

```bash
zrag() {
  (
    cd /opt/zglab-rag || exit 1
    sudo -u zglab env \
      PYTHONPATH=/opt/zglab-rag/app/src \
      /opt/zglab-rag/app/.venv/bin/python -m zglab_rag.cli "$@"
  )
}
```

之后所有用户管理命令都可以写成 `zrag ...`。

---

## 2. 查看用户

### 列出全部用户

```bash
zrag user list
```

输出包含 username、id、role、status、created_at、activated_at；不打印密码、session token 或 credential token。

### 查看单个用户

```bash
zrag user show <username>
```

例如：

```bash
zrag user show zhigao
```

---

## 3. 创建用户

### 创建普通用户

```bash
zrag user create <username>
```

等价于：

```bash
zrag user create <username> --role USER
```

示例：

```bash
zrag user create alice --role USER
```

创建成功后 CLI 只打印一次 activation URL：

```text
https://ask.zglab.fun/activate#token=...
```

用户初始状态为 `PENDING`，管理员不知道用户最终密码；用户打开 activation URL 后自行设置密码，
成功后状态变为 `ACTIVE`。

activation URL 是敏感的一次性凭证：

- 只通过可信渠道发送给对应用户；
- 不粘贴到聊天、工单或公开日志；
- 不长期保存；
- 默认有效期由生产配置控制；
- token 在 auth.db 中只保存 hash。

### 创建管理员

```bash
zrag user create <username> --role ADMIN
```

只在确实需要管理权限时创建 ADMIN；普通使用者保持 USER。

---

## 4. 禁用 / 启用用户

### 禁用用户

```bash
zrag user disable <username>
```

示例：

```bash
zrag user disable alice
```

禁用会同时撤销该用户现有 sessions；已有登录态立即失效，后续登录被拒绝。

### 重新启用已禁用用户

```bash
zrag user enable <username>
```

示例：

```bash
zrag user enable alice
```

该命令用于重新启用已禁用账号；不要把它当作 activation 或 password reset 的替代流程。

---

## 5. 重置密码

管理员发起密码重置：

```bash
zrag user reset-password <username>
```

示例：

```bash
zrag user reset-password alice
```

行为：

1. 立即撤销该用户现有 sessions；
2. 对已激活用户将凭证状态置为 `RESET_REQUIRED`；
3. 旧密码立即停止工作；
4. CLI 打印一次性 reset URL；
5. 用户通过 reset URL 设置新密码后恢复正常登录。

reset URL 形式：

```text
https://ask.zglab.fun/activate#token=...&purpose=reset
```

同样属于敏感一次性凭证，不要记录或公开传播。

如果目标用户尚未完成首次 activation，CLI 会按身份服务的实际状态生成适用的一次性 credential URL；
运维以 CLI 输出的 purpose/note 为准。

---

## 6. 强制撤销 Sessions

只撤销当前全部登录态，不修改账号启用状态或密码：

```bash
zrag user revoke-sessions <username>
```

示例：

```bash
zrag user revoke-sessions alice
```

适用场景：

- 怀疑某次登录态泄露；
- 希望强制用户重新登录；
- 用户设备丢失；
- 做认证相关生产验证。

---

## 7. Auth 数据库维护

显式检查/初始化生产 auth.db：

```bash
zrag auth init
```

生产正常运行后该命令主要用于确认 schema；不要随意删除并重新初始化已有 `auth.db`。

生产 auth.db：

```text
/opt/zglab-rag/runtime/auth.db
```

预期权限：

```text
600 zglab:zglab
```

---

## 8. 用户管理常用速查

```bash
# 先定义函数（每个新的 SSH shell 执行一次）
zrag() {
  (
    cd /opt/zglab-rag || exit 1
    sudo -u zglab env \
      PYTHONPATH=/opt/zglab-rag/app/src \
      /opt/zglab-rag/app/.venv/bin/python -m zglab_rag.cli "$@"
  )
}

# 列出用户
zrag user list

# 查看用户
zrag user show <username>

# 创建普通用户
zrag user create <username> --role USER

# 创建管理员
zrag user create <username> --role ADMIN

# 禁用用户（同时撤销 sessions）
zrag user disable <username>

# 重新启用用户
zrag user enable <username>

# 发起密码重置（旧密码立即失效，sessions 撤销）
zrag user reset-password <username>

# 仅撤销所有 sessions
zrag user revoke-sessions <username>

# 检查 auth.db schema
zrag auth init
```

---

## 9. 生产安全约束

用户管理时遵守以下原则：

- 不把密码作为 CLI 参数传入；系统设计上也不需要管理员设置用户密码；
- activation/reset URL 只显示一次，按 secret 处理；
- 不打印或复制 `auth.db` 内容到公共渠道；
- 不修改 `auth.db` 文件权限为 group/world readable；
- 不通过 SQLite 手工 UPDATE 用户状态代替 CLI；CLI 同时处理 session revoke、credential 状态和审计；
- 用户离开项目或不再允许访问时优先 `user disable`，而不是直接删数据库记录；
- 怀疑 session 泄露时优先 `user revoke-sessions`；
- 怀疑密码泄露时使用 `user reset-password`；
- 生产用户变更后可执行一次 `zglab-rag-backup.service`，确保最新 auth 状态进入备份。

相关设计：`docs/authentication.md`、`docs/api-v2.md`。
