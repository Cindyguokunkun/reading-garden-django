"""阅见应用的视图包。

本包按业务领域拆分视图模块，并在此汇总导出最常用的视图函数，
便于 URL 配置直接从 :mod:`reading.views` 导入。

子模块划分：
    - :mod:`reading.views.core`: 仪表盘、后台增删改与 Excel 导出。
    - :mod:`reading.views.quiz`: 测验开始、作答与回顾。
    - :mod:`reading.views.auth`: 各角色登录、登出与书架。
    - :mod:`reading.views.library`: 书库浏览与书籍编辑工具。
    - :mod:`reading.views.manage`: 管理员批量导入。
    - :mod:`reading.views.ranks`: 排行榜。
"""

from .core import action, dashboard, export_excel
from .quiz import quiz_review, quiz_start, quiz_take

__all__ = ['dashboard', 'action', 'export_excel', 'quiz_start', 'quiz_take', 'quiz_review']
