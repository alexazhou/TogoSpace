"""所有测试用例的基类，负责统一初始化和清理所有 service 的全局状态。"""
import service.message_bus as message_bus
import service.room_service as room_service
import service.agent_service as agent_service
import service.func_tool_service as func_tool_service
import service.scheduler_service as scheduler


class ServiceTestCase:
    """基础测试类：每个用例前重置所有 service 状态，用例后清理。

    使用 pytest 的 setup_method / teardown_method 钩子（对应 unittest 的 setUp / tearDown）。
    子类可重写这两个方法，但须在首行调用 super()。
    """

    def setup_method(self):
        message_bus.init()
        room_service.close_all()
        agent_service.close()
        func_tool_service.close()
        scheduler.stop()

    def teardown_method(self):
        scheduler.stop()
        func_tool_service.close()
        agent_service.close()
        room_service.close_all()
        message_bus.stop()
