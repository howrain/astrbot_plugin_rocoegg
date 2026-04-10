# 洛克王国查蛋器 - RocoEgg v2.1

基于 [RocoEgg](https://github.com/mfskys/rocomegg) 数据源的 AstrBot 查蛋插件。

## 功能

- 🔍 **智能查蛋**：根据尺寸和重量查询蛋对应的精灵
- 🔄 **一键同步**：支持从 GitHub 同步最新数据
- 🎯 **模糊匹配**：查询不到时自动推荐最接近的精灵

## 安装

1. 下载插件并放入 `AstrBot/data/plugins/` 目录
2. 重启 AstrBot 或重载插件
3. 执行 `/同步蛋数据` 获取数据

## 指令

| 指令 | 说明 |
|------|------|
| `/查蛋 <尺寸> <重量>` | 查询蛋对应的精灵 |
| `/同步蛋数据` | 从 GitHub 同步最新数据 |
| `/蛋数据状态` | 查看数据同步状态 |
| `/rocoegg帮助` | 显示帮助信息 |

**示例：**
```
/查蛋 0.25 14.5
```

## 数据收集

发现新蛋数据？提交到上游表格：
https://f.wps.cn/ksform/w/write/YUmapbHA/

## 数据来源

- [mfskys/rocomegg](https://github.com/mfskys/rocomegg)

## License

MIT
