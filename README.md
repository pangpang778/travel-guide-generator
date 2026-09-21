# Travel Guide Generator

一个给普通人使用的旅游攻略 Skill。

你只需要告诉 AI：

- 从哪里出发
- 去哪里，或有哪些候选目的地
- 哪天出发
- 玩几天、住几晚
- 想参考哪个平台

Skill 会自动查询真实出行数据，比较候选目的地，并生成可以直接打开的完整攻略。

## 只需要一个命令

~~~text
/travel-guide
~~~

信息不完整时，Skill 会先反问你缺少什么，不会猜日期、地点或行程长度，也不会提前访问平台。

完整示例：

~~~text
/travel-guide 从杭州出发，2026-10-01，北京/上海候选，3天2夜，参考小红书，交通只用12306
~~~

## 小白安装

最简单的方法：把下面整段复制给你的 AI 助手，让它帮你安装。

~~~text
请帮我安装这个旅游攻略 Skill：
https://github.com/pangpang778/travel-guide-generator

安装完成后，我只使用 /travel-guide。
请自动检查并安装需要的依赖，不要让我手动配置 API Key。
如果平台需要浏览器登录，请告诉我打开浏览器登录，不要向我要密码、Cookie 或验证码。
安装完成后，确认 Skill、Agent Reach、OpenCLI 和 12306 后端可用。
~~~

也可以手动安装：

~~~bash
npx skills add pangpang778/travel-guide-generator@travel-guide-generator -g -y
~~~

本地安装脚本：

Windows：

~~~powershell
.\install.ps1
~~~

macOS/Linux：

~~~bash
./install.sh
~~~

安装脚本会自动配置 Agent Reach、OpenCLI、社交平台后端和 12306 读取后端。

## 数据来源

### 出行数据：12306

交通信息固定使用 12306，包括：

- 车站匹配
- 直达车次
- 出发和到达时间
- 行程时长
- 余票状态
- 座位价格
- 车次经停站
- 去程和返程比较

没有具体日期时，Skill 不会伪造余票，会先询问日期。

### 攻略数据：社交平台可切换

默认参考小红书，也可以在请求中指定其他平台：

~~~text
参考小红书
参考 Twitter
参考 Reddit
参考 B站
参考小红书和 B站
~~~

支持的参考平台：

- 小红书：路线、餐厅、住宿、避坑和真实游记
- Twitter/X：实时讨论和当地动态
- Reddit：英文旅行经验和踩坑信息
- B站：视频攻略和路线讲解

平台需要登录时，使用你自己浏览器中的登录状态。Skill 不会读取、索要或上传密码和 Cookie。

## 会生成什么

每次规划会先生成候选对比，再生成最终攻略：

- 候选目的地优缺点
- 去程/返程交通比较
- 每日详细行程
- 景点顺序和时间安排
- 住宿区域建议
- 当地美食建议
- 预算估算
- 社交平台参考来源
- 避坑提醒
- 天气和室内备选建议

输出文件：

- HTML：直接双击打开，适合手机和电脑
- Markdown：方便复制和编辑
- ICS：导入日历
- GeoJSON：导入地图工具
- JSON：机器可读的完整行程

页面原型案例：打开 [examples/travel-guide-prototype.html](examples/travel-guide-prototype.html)，查看“宁波出发、苏州/重庆/成都/福州候选、3天2夜”的结论型攻略页面。

## 缺信息时怎么处理

Skill 必须先拿到：

1. 出发地
2. 目的地或候选目的地
3. 出发日期，格式为 YYYY-MM-DD
4. 游玩天数或晚数

例如只输入：

~~~text
/travel-guide 我想去北京玩
~~~

Skill 只会反问：

~~~text
请补充：
1. 从哪里出发？
2. 哪天出发？
3. 玩几天或住几晚？
~~~

信息补齐后才会查询社交平台和 12306。

## 安全边界

这是只读规划工具：

- 不自动购票
- 不支付
- 不预订酒店
- 不发布小红书内容
- 不点赞、评论或关注
- 不管理社交账号

车票、价格、营业时间和平台内容都会变化。攻略中的实时数据应在出发前再次核验。

## 给开发者

普通用户不需要使用下面的命令。它们是 Skill 内部实现：

~~~text
scripts/plan_trip.py       采集并生成候选对比
scripts/research_trip.py   调用参考平台和 12306
scripts/compare_trip.py    生成候选对比 HTML
scripts/build_guide.py     生成单城市 HTML/Markdown/ICS/GeoJSON
~~~

## 许可证

MIT License。

项目地址：

https://github.com/pangpang778/travel-guide-generator
