NovelHub_Project_Specification.md



```shell
# NovelHub
# Personal Novel Library System
# 开源级个人小说数字图书馆项目需求文档

版本：v1.0
项目类型：Self-hosted Personal Digital Library
目标平台：Docker / NAS
目标设备：绿联 DX4600 Pro

---

# 1. 项目目标

开发一个开源级个人小说数字图书馆系统。

系统用于：

1. 自动从小说网站同步小说
2. 保存小说正文和元数据
3. 自动更新连载章节
4. 提供 Web 阅读界面
5. 提供全文搜索
6. 提供收藏、阅读进度管理
7. 支持多个小说网站插件
8. 支持 AI 阅读辅助
9. 支持 NAS 长期运行


项目定位：

不是简单爬虫。

而是：

> 一个可长期维护、可扩展、多来源的个人小说管理平台。


---

# 2. 核心设计原则


## 2.1 插件化

任何小说网站必须通过插件接入。


例如：
crawler/plugins/

alicesw/

qidian/

fanqie/

custom/


新增网站：

只增加插件。

不修改核心代码。



---

## 2.2 数据与正文分离


数据库：

保存：

- 小说信息
- 作者
- 标签
- 章节索引
- 更新时间
- 阅读记录


正文：

独立存储。


推荐：

Markdown 格式。


例如：


books/

└── author/

  └── book/

       ├── metadata.json

       ├── 000001.md

       ├── 000002.md


---

## 2.3 增量同步


禁止重复下载。


同步逻辑：


获取目录

↓

比较数据库

↓

发现新增章节

↓

下载

↓

更新索引

↓

生成日志



---

## 2.4 长期运行


必须支持：

- 自动恢复
- 自动重试
- 日志
- 备份
- 数据迁移



---

# 3. 技术栈


## 后端

Python 3.13

FastAPI

SQLAlchemy 2.x

Alembic

Pydantic v2



## 数据库

PostgreSQL 17


用途：

保存：

- 小说
- 作者
- 标签
- 用户
- 阅读记录
- 同步状态



## 缓存

Redis


用途：

- 任务队列
- 缓存
- 限速



## 搜索

Meilisearch


用途：

全文搜索：

- 小说名
- 作者
- 标签
- 正文



## 定时任务

Celery + Redis


用途：

每日同步。



## 爬虫

requests

BeautifulSoup4

Playwright


支持：

普通 HTTP

JavaScript 渲染页面



## 前端

Vue3

TypeScript

Vite

Pinia

TailwindCSS



## 部署

Docker Compose

Nginx



---

# 4. 系统架构


          Internet

              |

              |

      Crawler Engine

              |

              |

    Metadata Processor

      /              \

PostgreSQL File Storage

              |

              |

        Meilisearch


              |

              |

          FastAPI


              |

              |

          Vue Web


---

# 5. Docker 服务


docker-compose 需要包含：



postgres

redis

meilisearch

crawler

scheduler

backend

frontend

nginx

backup



可选：


watchtower

prometheus

grafana




---

# 6. 数据库设计


## sources

小说来源网站


字段：

id

name

url

plugin_name

enabled



---

## authors


作者


字段：

id

name

description



---

## books


小说


字段：

id

source_id

title

author_id

cover

description

status

created_at

updated_at



---

## chapters


章节


字段：

id

book_id

chapter_number

title

content_path

hash

created_at



---

## tags


标签



---

## book_tags


小说标签关系



---

## crawl_tasks


同步任务


字段：

id

source

status

started_at

finished_at

error



---

## crawl_logs


日志


---

## cookies


保存登录 Cookie


字段：

id

source

cookie_data

expired_at



---

## reading_progress


阅读进度


字段：

user

book

chapter

position



---

## book_versions


章节历史


保存：

- 修改记录
- 删除记录
- 内容 hash



---

# 7. Cookie 登录设计


优先：

Cookie。


流程：



用户浏览器登录网站

    |

复制 Cookie

    |

后台保存

    |

Crawler 使用




后台提供：


Cookie管理

测试Cookie

更新时间

失效提醒



备用：

账号密码自动登录。



---

# 8. Crawler 插件设计


目录：


crawler/plugins/alicesw/

login.py

parser.py

crawler.py

updater.py

config.py




统一接口：


```python

class NovelPlugin:


    def login():
        pass


    def get_books():
        pass


    def get_book():
        pass


    def get_chapters():
        pass


    def get_chapter():
        pass


    def update():
        pass

9. 事件系统

采用事件驱动。

例如：

ChapterUpdated


      |

      |

-----------------

|       |        |

Search AI    Backup


事件：

BookCreated

BookUpdated

ChapterCreated

ChapterUpdated

SyncFailed

CookieExpired

10. Web 功能
首页

显示：

最近更新
最近阅读
收藏
分类
搜索

支持：

书名
作者
标签
正文
阅读页面

支持：

上一章
下一章
目录
夜间模式
字体调整
阅读记录
用户系统

支持：

管理员

普通用户

游客

11. 后台管理

功能：

网站管理

Cookie管理

同步管理

日志查看

索引管理

备份恢复

系统状态

12. AI 扩展

预留接口：

/ai/chat

/ai/summary

/ai/person

/ai/timeline


支持：

OpenAI API

Claude API

Qwen

Ollama

Hermes

13. RAG 支持

未来支持：

小说

↓

章节切片

↓

Embedding

↓

向量数据库

↓

AI问答


支持：

人物关系

剧情总结

世界观分析

14. 存储抽象

不要绑定本地磁盘。

统一接口：

Storage


LocalStorage


S3Storage


WebDAVStorage


默认：

本地 NAS。

未来支持：

MinIO。

15. 安全设计

必须：

密码加密
Cookie 加密保存
API Token
权限控制
HTTPS支持
16. 备份设计

每日：

增量备份

每周：

完整备份

备份内容：

postgres

books

covers

config


格式：

tar.zst

17. NAS 优化

针对：

绿联 DX4600 Pro

优化：

Docker volume
PostgreSQL 参数
日志轮转
CPU限制
内存限制
SSD缓存
18. 开发阶段
Stage 0

项目初始化

输出：

项目目录
Docker Compose
环境配置
README
Stage 1

核心框架

实现：

数据库
API
插件系统
存储系统
日志
Stage 2

AliceSW 插件

实现：

Cookie登录
小说列表
小说详情
章节同步
增量更新
Stage 3

Web系统

实现：

阅读器
搜索
用户系统
Stage 4

高级功能

实现：

AI
RAG
EPUB
多来源
移动端
19. 开发要求

AI 开发时必须：

不改变已有架构
保持 Docker 化
提供完整文件
提供运行命令
提供测试方法
不输出伪代码
不省略关键代码
20. 当前第一开发任务

开始生成：

Stage 0 项目初始化。

要求输出：

完整目录结构
docker-compose.yml
.env.example
README.md
初始化数据库配置
FastAPI基础项目
Vue3基础项目

生成后进入 Stage 1。

```

