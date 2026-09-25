import uvicorn

from .config import get_settings
from .factory import create_app


if __name__ == "__main__":
    settings = get_settings()
    # 冻结环境下字符串导入依赖 PyInstaller 的模块发现，直接传入 app
    # 可避免二次导入失败；源码运行也保持相同路径。
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, reload=False)
