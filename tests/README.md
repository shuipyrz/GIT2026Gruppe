# GeoChat 测试运行说明

当前测试直接调用 agent/、gis/、maps/、core/ 和 frontend.py；backend.py 已移除。
Phase 2 报告及 CASE_RESULTS.md 保留为历史快照，不代表当前测试全集。

## 环境与运行

本次验证环境为 Windows x64 / CPython 3.14.5。它是本次新建的测试环境，
不是此前团队运行环境的恢复结果。其他 Python 版本尚未验证。

```powershell
# 已有 .venv 时直接运行，不需要联网或任何真实 API Key。
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short --junitxml=tests/results.xml
.venv\Scripts\python.exe tests/summarize_results.py
# 可选：真实本机服务、HTTP 与 WebSocket 页面启动检查，执行后自动停止。
.venv\Scripts\python.exe tests/check_startup.py

# 新环境：用已安装的 Python 创建隔离环境，再安装测试所需依赖。
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r tests/requirements.txt
```

本次 .venv 使用 uv 创建，可能没有 pip；也可使用：

```powershell
uv pip install --python .venv\Scripts\python.exe -r tests/requirements.txt
```

安装依赖需要包源访问权限；安装完成后核心测试不访问互联网。
`requirements.txt` 固定直接依赖，`constraints.txt` 记录本次完整解析版本。
`.venv/`、uv 缓存、pytest 缓存不提交。
`tests/logs/`、`tests/results.xml` 为本地生成证据，也不提交。
`CASE_RESULTS.md` 是审核后保留的逐项基线记录；仅在有意更新基线时重新生成。

Streamlit 预期启动命令为：

```powershell
.venv\Scripts\python.exe -m streamlit run frontend.py
```

空白页面启动无需 Key；实际对话和上传元数据分析需要模型配置。启动实测边界见 PHASE3E_REPORT.md。

## 测试边界

- `conftest.py` 创建独立正式 SessionAgent，用合成 Key 替换环境，禁用 dotenv 读取，并替换 Streamlit secrets。
- 整个测试执行期间禁止导入 backend；即使动态导入被业务捕获，fixture 收尾仍会报错。
- 仅模型构造、模型回答及 Nominatim HTTP 响应被替换；LangGraph、ToolNode、装饰工具、GIS 库、随机森林和 Folium 都是真实执行。
- 阻止 HTTP 客户端与外部 socket 连接。仅允许 Windows asyncio 所需的字面量 loopback 连接；即使业务捕获了网络阻断异常，fixture 收尾仍报错。
- 每个测试创建独立 Session；跨线程、交错执行和异步调用测试在同一进程建立多个实例，不用全局串行掩盖隔离问题。
- `AppTest` 执行 Streamlit Python 服务端及控件事件；不执行浏览器 JavaScript，不验证地图瓦片、离线资源或真实下载交互。
- 上传控件边界使用 Mock；HTML 内存下载和临时目录字节比较不验证浏览器实际下载，不产生仓库根目录地图文件。

## Regression 与 Correctness

| 文件 | 内容 |
|---|---|
| `test_tools.py` | 24个工具的注册、参数名/默认值/必填参数、调用、异常、状态及数据 |
| `test_data_maps.py` | CSV/JSON/GeoJSON、点线面、地图、专题图、HTML、元数据模型替身 |
| `test_agent_session.py` | 实际 LangGraph、脚本化模型、多步骤、失败、状态、Streamlit 控件 |
| `test_gis_correctness.py` | 解析几何基准与 WGS84 椭球独立参考；正确性期望 |
| `test_known_defects.py` | 有确定复现的接口、渲染及输入校验历史缺陷 |
| `test_gis_core.py` | GIS 公开入口、输入保护、CRS 及独立进程 |
| `test_map_renderer.py` | 全部地图类型、样式、顺序和内存 HTML |
| `test_agent_modules.py` | 模型配置、延迟初始化、24个工具的绑定及工具分派 |
| `test_session_contracts.py` | 所有权、生命周期、并发/异步隔离与原子提交 |
| `test_app_integration.py` | 门面移除、依赖方向、导入顺序及完整服务端 UI 调用链 |

GIS 参考包括：已知米制坐标的矩形面积和间距；WGS84 Geod 直接解生成的 100m 点对；
Geod 反解检验 Buffer 半径；椭球多边形面积；明确面内/顶点/面外点及重叠区计数。
Geod 与业务采用的平面距离/面积计算路径不同，但底层仍共享 PROJ；不是外部测绘认证。
缓冲区测试直接通过 WGS84 Geod 反解验证地面半径，不要求结果复现旧 EPSG:3857 算法。
投影输入、WGS84输出和输入不变的断言同时保留。

HTML导出测试通过聊天调用 `export_current_map` 并检查下载附件、图层开关及底图来源声明；
不再寻找旧导出按钮。批量工具调用场景按实际结果ID重新规划后续步骤，
缺失图层在执行控制器中被拦截时检查任务日志与状态不变。
原子提交测试确认不可序列化属性到达提交边界；计算异常测试确认模拟函数确实修改了
隔离的计算输入后报错，再检查源输入、两个会话的数据和图层没有变化。

已知缺陷采用 `xfail(strict=True)`，并限定预期异常类型：

- `XFAIL` = Known Defect，绝不等于 Passed。
- `XPASS` 导致失败，修复算法后必须审核并移除对应标记。
- 其他异常和未标记失败会导致非零退出码。
- 若要观察历史缺陷的真实失败，可运行 `pytest --runxfail`；不将该模式的失败计入普通基线通过率。

边界点排除、重叠面重复计数、部分结果保留及历史缺工具轨迹被记录为当前行为，
不表示已经批准这些业务语义。GIS 容差仅是本次测试标准，不能替代产品精度验收。

## 后续重构如何使用

每批修改先运行全集，再检查 Known Defect 是否增加或出现 XPASS。
最终模块已建立；后续修改保留输入数据、独立参考值和用户行为断言，优先适配正式接口。
GIS 算法修复与文件搬迁分别审核，不降低断言或放宽容差以消除回归。

2026-11-24 在上述锁定环境运行完整测试：**423 passed，0 failed，9 xfailed**。
此前37条未通过的测试已完成核查与修复，并将工具参数契约覆盖扩展到24个工具。
缓冲区半径与零线宽测试已移除过期的预期失败标记；未增加新的 `xfail` 或跳过项。

前端布局验收：运行 `python tests/feedback_browser.py --workspace-layout`，使用本地模型和合成数据
实际上传0/1/20个文件，检查上传框高度不变、文件可滚动和移除、原始数据删除不会移除已有地图层，
以及1024×768至1920×1080桌面视口下地图、聊天输入和侧栏操作均在一屏内。
长列表与聊天内容在各自区域内滚动；小屏幕保留页面滚动以保证内容可达。

剩余9条预期失败仍表示实际已知缺陷，不计为通过：

| 问题 | 用例数 |
|---|---:|
| 极小面积提前舍入为0，导致面积或密度不正确 | 1 |
| 密度使用提前舍入的面积，导致偏差 | 1 |
| 空面、零面积面、自交面未被面积分析正确拒绝 | 3 |
| 已有可见图层时，缓冲分析按名称查找原始数据失败 | 1 |
| 指定的密度点图层不存在时，退化成仅计算面积 | 1 |
| 直接将单个GeoJSON Feature交给点渲染函数时不显示 | 1 |
| CSV中越界的纬度未被解析器拒绝 | 1 |

详细原因可运行 `pytest tests -q -rx --tb=short` 查看。
PHASE2–PHASE4报告及CASE_RESULTS.md保留为历史审核记录，不代表当前结果；
results.xml可选本地生成，不自动覆盖旧审核记录。
