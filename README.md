# 洛克王国查蛋器 - RocoEgg v2.3.0

基于 [RocoEgg](https://github.com/mfskys/rocomegg) 数据源的 AstrBot 查蛋插件。

## 功能

- 智能查蛋：根据尺寸和重量查询蛋对应的精灵
- 手动同步：支持手动拉取最新蛋数据
- 定时同步：支持从 WebUI 配置 cron，默认每天凌晨 1 点自动同步
- 代理加速：支持通过 `github_proxy_url` 配置 GitHub 代理地址
- 失败通知：定时同步失败时可按 UMO 目标主动发送通知

## 安装

1. 将插件放入 `AstrBot/data/plugins/`
2. 安装依赖
3. 重启 AstrBot 或重载插件
4. 在 WebUI 中按需配置代理、定时同步和通知 UMO
5. 执行 `/同步蛋数据` 初始化本地数据

## WebUI 配置

| 配置项 | 说明 |
|------|------|
| `github_proxy_url` | GitHub 代理加速地址。留空时直连 GitHub，支持直接填写前缀，如 `https://ghfast.top/`，也支持模板形式 `https://your-proxy.example.com/{url}` |
| `auto_sync_enabled` | 是否启用定时同步，默认开启 |
| `auto_sync_cron` | 标准 5 段 cron 表达式，默认 `0 1 * * *`，即每天凌晨 1 点 |
| `auto_sync_notify_target` | 定时同步失败通知 UMO。留空则不主动通知，示例：`獭獭:FriendMessage:942648152` |

## 指令

| 指令 | 说明 |
|------|------|
| `/查蛋 <尺寸> <重量>` | 查询蛋对应的精灵 |
| `/同步蛋数据` | 手动同步最新蛋数据 |
| `/蛋数据状态` | 查看本地数据状态、定时同步状态和当前 cron |
| `/rocoegg帮助` | 显示帮助信息 |

示例：

```text
/查蛋 0.25 14.5
```

## 定时同步说明

- 默认启用，默认 cron 为 `0 1 * * *`
- cron 解析失败时不会启动定时任务
- 定时同步成功不会主动发消息
- 定时同步失败时，如果配置了 `auto_sync_notify_target`，会向对应 UMO 发送通知

## 数据收集

发现新蛋数据可提交至：
https://f.wps.cn/ksform/w/write/YUmapbHA/

## 数据来源

- [mfskys/rocomegg](https://github.com/mfskys/rocomegg)

## License

MIT
