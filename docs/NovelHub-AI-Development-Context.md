# NovelHub AI Development Context V2

# Project: NovelHub - Personal Novel Digital Library

---

# 1. 项目定位

项目名称：

> NovelHub - Personal Novel Digital Library

定位：

> 一个长期运行、自托管、可扩展的个人小说数字资产管理平台。

不是：

* 小说下载脚本
* 单网站爬虫
* 简单阅读器

目标：

构建：

```
互联网小说资源

↓

自动采集

↓

本地永久保存

↓

Metadata管理

↓

全文搜索

↓

Web阅读

↓

阅读记录

↓

知识库

↓

AI/RAG辅助阅读
```

---

# 2. 当前项目状态

当前仓库：

已经完成：

* Docker 环境
* Backend
* Frontend
* PostgreSQL
* API 基础框架
* 数据模型
* Storage 抽象
* Search 接口
* Scheduler 基础
* Plugin 基础
* AI接口预留

当前状态：

> 已进入验收、调试、功能补全阶段。

禁止：

* 推倒重写
* 新建完全独立项目
* 替换整体技术栈

---

# 3. 当前核心问题

## 原设计问题

原设计：

```
一个网站

↓

一个Python Plugin

↓

手写Crawler
```

例如：

```
crawler/plugins/

alicesw/

qidian/

fanqie/
```

存在问题：

* 开发成本高
* 网站变化需要维护代码
* 无法快速扩展大量网站
* 重复实现解析逻辑

---

# 4. 新架构调整目标

## 核心原则

保持：

```
NovelHub Core
```

不变。

只替换：

```
Crawler Layer
```

由：

```
Code Driven Crawler
```

升级为：

```
Rule Driven Source Engine
```

---

# 5. 新整体架构

```
                    NovelHub


                        

Frontend

↓

Backend API

↓

Business Service

↓

Repository

↓

Database



-------------------------------



Crawler Service


↓

Source Engine


↓

Book Source Rules


↓

Parser


↓

Novel Pipeline


↓

Storage


↓

Database


↓

Search Index

```

---

# 6. 模块职责重新定义

## backend

负责：

* API
* 用户
* 阅读记录
* 收藏
* 标签
* 搜索接口
* AI接口

禁止：

* 网站解析
* HTTP采集逻辑

---

# crawler

调整为：

## Source Engine

负责：

* 加载书源规则
* 执行搜索
* 获取小说信息
* 获取章节
* 获取正文
* 登录Cookie
* 请求管理

禁止：

* Web API
* UI逻辑

---

# scheduler

负责：

* 定时同步
* 更新任务
* Retry
* Queue

例如：

```
每天凌晨


↓

检查收藏小说

↓

执行Source Engine

↓

发现更新

↓

下载新章节

```

---

# frontend

负责：

* 阅读界面
* 搜索界面
* 管理界面

禁止：

业务逻辑。

---

# storage

统一接口：

```
Storage

save_book()

save_chapter()

save_cover()

read()

delete()

exists()

```

支持：

当前：

```
Local Storage
```

未来：

```
MinIO

S3

WebDAV
```

禁止：

业务层直接：

```
open()

write()
```

---

# 7. 新增 Source Engine

目录：

建议：

```
crawler/

├── engine/

│   ├── parser.py

│   ├── request.py

│   ├── executor.py

│   └── loader.py


├── sources/

│   ├── xxx.json

│   ├── xxx.yaml


├── service/

│   ├── sync.py

│   └── downloader.py

```

---

# 8. Source规则设计

原则：

规则驱动。

禁止：

```
if website == xxx:

```

必须：

```
加载规则

↓

执行规则

```

---

规则需要支持：

## 搜索

例如：

```
search_url
```

## 小说信息

支持：

```
title

author

cover

description

category
```

## 章节列表

支持：

```
chapter_url

chapter_title

chapter_order
```

## 正文解析

支持：

```
content_selector

clean_rule

```

---

# 9. 开源书源兼容

目标：

支持导入已有开源阅读生态书源。

优先：

兼容成熟规则格式。

例如：

```
书源JSON

↓

转换层

↓

NovelHub Source Model

↓

执行

```

不要：

重新创建新的书源生态。

---

# 10. 数据流

完整流程：

```
Source

↓

Search Book

↓

Book Metadata

↓

Chapter List

↓

Chapter Content

↓

Markdown/Text

↓

Storage

↓

Database Metadata

↓

Search Index

↓

Web Reader

↓

AI/RAG

```

---

# 11. 数据库存储原则

数据库保存：

## Book

* 标题
* 作者
* 来源
* 分类
* 状态

## Chapter

* 标题
* 顺序
* Hash
* 更新时间

## Source

* 来源信息
* 规则来源

## Reading

* 用户阅读进度

禁止：

数据库保存完整正文。

---

# 12. 正文存储

正文：

Storage。

格式：

推荐：

```
Markdown
```

结构：

```
storage/

books/

  book_id/

      chapter_001.md

      chapter_002.md

```

---

# 13. 更新机制

禁止：

全量重新下载。

必须：

```
获取最新章节

↓

Hash比较

↓

发现新增

↓

下载

↓

保存

↓

更新索引

```

支持：

* 断点恢复
* 失败重试
* 增量更新

---

# 14. AI/RAG设计

AI只消费：

```
Book

↓

Chapter

↓

Embedding

↓

Vector Database

↓

LLM
```

AI禁止：

参与：

* 爬取
* 解析
* 下载

---

# 15. 当前最高优先级

## P0

### 1. 重构Crawler层

目标：

建立：

```
Source Engine
```

---

### 2. 支持书源规则

完成：

```
导入书源

↓

搜索小说

↓

获取章节

↓

下载正文
```

---

### 3. 打通完整闭环

必须实现：

```
书源

↓

小说

↓

章节

↓

正文

↓

Storage

↓

Database

↓

Search

↓

Web阅读
```

---

# 16. 修改要求

AI修改代码时必须：

1. 阅读当前仓库代码。
2. 不破坏已有模块。
3. 优先复用已有实现。
4. 不重新设计Backend。
5. 不重新设计Frontend。
6. 不修改数据库语义。
7. 不删除已有接口。
8. 保持Docker运行。
9. 提供数据库迁移。
10. 提供测试方案。

---

# 17. 禁止事项

禁止：

* 删除当前项目重新开始。
* 替换技术栈。
* 引入新的大型框架。
* 将正文存入数据库。
* 为单网站写死逻辑。
* 创建大量重复Crawler。

---

# 18. 开发流程

每次修改：

必须：

## 第一步

分析当前代码：

输出：

```
已有功能

缺失功能

问题原因
```

---

## 第二步

设计方案：

说明：

```
修改哪些文件

新增哪些文件

影响范围
```

---

## 第三步

实施：

提供：

* 完整代码
* 数据库迁移
* 配置修改

---

## 第四步

验收：

验证：

```
docker启动

↓

登录

↓

导入书源

↓

搜索小说

↓

下载章节

↓

Web阅读

↓

更新章节

```

---

# 19. 最终目标

NovelHub最终成为：

```
个人小说数字资产平台


支持：

大量小说来源

自动同步

本地永久保存

全文搜索

Web阅读

阅读记录

AI理解

RAG知识库


长期运行于NAS
```

---

# 当前开发方向总结

不要重写 NovelHub。

当前任务：

> 保留现有系统，将 Crawler 从“网站代码插件模式”升级为“开源书源驱动的 Source Engine 模式”。

这是一次架构升级，不是重新开发。