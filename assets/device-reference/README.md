# 首选器件参考 / Preferred device reference

用户授权提供的精选器件集合：25个实例、24种符号。打开 `devices.sxsch` 查看中文用途与引脚。
This is an editable, unwired reference sheet, not an executable circuit or an attachable submodule.

原始文件虽名为.sxcmp，内容没有外部.Symbol接口；本库以.sxsch保存。
Requires a licensed SIMetrix/SIMPLIS 8.40 installation for native editing and installed model/editor scripts.
Parameterized logic, waveform and summer models use the matching installed vendor resources. No vendor models or manuals are bundled.
Selected embedded symbol drawings/properties originate from the supplied sheet; vendor material retains its respective rights. The repository MIT license does not relicense vendor content.

Verified: native open, save and PNG/SVG export on 8.40. Individual electrical behavior is not universally certified.
R1 has MODEL=R and an example VALUE=1k. U2/U3 are duplicate buffer instances with both normal and inverted outputs.

|编号|中文名称|符号|用途|
|---|---|---|---|
|D01|电阻|`res`|限流、分压、RC时间常数|
|D02|电容|`cap`|滤波、积分、保持与定时|
|D03|电感|`ind`|Buck储能与电流纹波|
|D04|参考地|`gnd`|电路零电位与逻辑返回端|
|D05|网络标签|`term`|同名网络连接与信号命名|
|D06|直流电压源|`dc_source`|输入电源、偏置与基准|
|D07|直流电流源|`dc_isource`|恒定负载与偏置电流|
|D08|分段线性电流源|`ipwl`|负载跃变与任意电流激励|
|D09|可配置电压波形源|`vwave_v2`|当前为三角波；可编辑波形|
|D10|子模块端口|`modport`|建立层级模块的外部连接|
|D11|压控电压源|`vcvs`|电压增益、反相与差分|
|D12|压控电流源|`vccs`|跨导、电压转电流与积分|
|D13|电压控制开关|`simplis_prim_vcswitch`|功率开关、采样与复位|
|D14|分段线性电阻|`simplis_pwlr_box`|分段I-V关系与非线性近似|
|D15|双输入加法器|`SIMPLIS_SUMMER2`|加权相加；负系数可作相减|
|D16|比较器|`SIMPLIS_DIGI1_COMP_Y`|阈值判定；含正反输出|
|D17|缓冲与反相输出|`SIMPLIS_DIGI1_BUF_Y`|OUT同相；OUT_BAR反相|
|D19|双输入与门|`SIMPLIS_DIGI1_AND_2I0_Y`|两个条件同时成立|
|D20|三输入与门|`SIMPLIS_DIGI1_AND_3I0_Y`|三个条件同时成立|
|D21|双输入或门|`SIMPLIS_DIGI1_OR_2I0_Y`|任一条件成立|
|D22|SR锁存器|`SIMPLIS_DIGI1_SRLATCH_1_NONE_Y`|置位、复位及状态保持|
|D23|上升沿D触发器|`SIMPLIS_DIGI1_DFF_Y_0_TO_1`|边沿采样、状态切换与分频|
|D24|节点电压探针|`probev_new`|连接待测节点，输出电压波形|
|D25|串联电流探针|`InlineCurrentProbe`|串入支路；按箭头定义正方向|

详细引脚、参数默认值及证据见 `index.json`。查询工具读取原生符号定义，不依赖该表推测引脚。
Suggested additions when needed: an explicitly configured 0/5 V pulse example; appropriate SIMPLIS diode/MOSFET models; loop-injection devices. A buffer inverted output is already available.
