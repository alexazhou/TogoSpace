# Import 规范

## 分组顺序

每个文件的 import 分三组，组间用空行隔开：

```
# 第一组：系统标准库
import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

# 第二组：第三方包（pip install 的依赖）
import aiohttp
from pydantic import BaseModel

# 第三组：项目内部包
from route import xxx
from controller.xxx import xxx
from util import llm_api_util, config_util  # util 模块统一用一行引入
from model.xxx import ...
from service import aaa, bbb_service as bbb, ...   # service 模块用一行引入
from service.xxx import SpecificClass              # 具体类可单独一行
from constants import XxxEnum, XxxDataclass        # constants 放最后
```

## 规则说明

1. **标准库优先**：`import` 形式和 `from ... import` 形式均可，按字母序排列。

2. **第三方包次之**：仅包含通过 `pip` 安装的依赖（如 `aiohttp`、`pydantic`、`tornado`）。若当前文件无第三方依赖，省略该组。

3. **内部包放最后**，按以下子顺序排列：
   - `route` & `controller.*`：路由与控制层
   - `util`：工具层，**统一用一行** `from util import ...` 引入
   - `model.*`：数据层，纯数据定义
   - `service`：服务层模块，**统一用一行** `from service import ...` 引入，别名保持一致（见下表）
   - `service.XxxClass`：若需导入服务层中的具体类，紧跟在 `from service import ...` 之后
   - `constants`：枚举与常量，**始终放在最后一行**

## service 模块别名约定

| 模块 | 别名 |
|------|------|
| `scheduler_service` | `scheduler` |
| `room_service` | `chat_room` |
| `func_tool_service` | `agent_tools` |
| `agent_service` | 无（直接使用） |
| `llm_service` | 无（直接使用） |

## 示例

```python
# 标准库
import asyncio
import logging
from typing import Dict, List, Optional

# 第三方包
import tornado.web

# 内部包
from route import make_app
from controller.agent_controller import AgentHandler
from util import llm_api_util, config_util
from model.chat_model import ChatMessage
from service import agent_service, room_service as chat_room, func_tool_service as agent_tools
from service.agent_service import Agent
from constants import TurnStatus
```

