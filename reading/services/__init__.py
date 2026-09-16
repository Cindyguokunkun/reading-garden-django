"""阅见应用的服务层包。

本包封装与外部系统交互的独立服务模块，供视图层调用，
将网络请求、数据抓取与解析等细节与业务视图解耦。

子模块划分：
    - :mod:`reading.services.http`: 统一的 urllib 请求封装与错误类型。
    - :mod:`reading.services.arbookfinder`: AR BookFinder 书籍检索与详情抓取。
    - :mod:`reading.services.covers`: Open Library / Google Books 封面检索。
    - :mod:`reading.services.quizgen`: 基于 AI 的阅读理解题目生成与校验。
"""
