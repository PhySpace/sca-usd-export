# 产品包声明的圆截面刚体关节线缆

适用当前候选 `sca-usd-export==0.1.8rc2` 与 `sca-physics==0.1.8rc1`，SCA 2.2.5。Schema1.5只将刚体链physics_hz上限扩至16384，原1.4仍限4096；旧1.4/1.5分段2..512不变；1.6以保存的N与可配置budget检查，不重选N。描述入口见payload docs/automatic-cables.zh-CN.md，准确兼容与升级边界见payload docs/compatibility-0.1.8rc2.zh-CN.md。这是后附加流程，不调用暂停中的 0.2 authoring API。

Schema 1.7 保留严格来源、可重算派生值及任务决策；导出只消费已保存结果。部署与输入哈希保护见安装 payload 的 docs/agent-decisions.zh-CN.md。
先由 SCA 建模并 capture；用物理插件 `inspect` 选择精确 part occurrence `node_id`，用 `set-rigid-chain --target ID --config chain.json --output final.scadpkg` 保存中心线和全部配置，独立进程 `validate` 后再正常 `export final.scadpkg output.usda`。导出器直接构造刚体链，不生成 FEM 中间资产，不调用 V6 项目 builder，不读取旧 USD。外部中心线 JSON 仅用于附加输入，重新导出只需最终产品包。

第一版支持明确提供的 world SI m 中心线、圆截面、直线及平面初始折线，可选端部 relief、任意合法自由区分段数，`free`、`world_fixed`、`body_fixed` 端部。不会从任意 CAD 或两个端点恢复完整中心线。缺少数据、单位错误、CAD 绑定过期、目标冲突和未知字段必须报错。

`linear_density` 是 kg/m，逐段质量按材料弧长分配；胶囊弦长加两端半球用于碰撞和显示，其重叠不增加质量。惯量是分配此质量后的实心胶囊近似。EI 和 GJ 是 N*m²，分别按相邻材料区间平均弧长 ell 换算为关节 k=EI/ell、GJ/ell。`joint_damping` 是每关节 N*m*s/rad；更改段数会改变整体阻尼，不能称为连续分布阻尼。USD 每度驱动系数和长度单位由插件统一换算。

`rest_state=straight`、`frame_policy=shortest_arc_x` 表示直线自然态，初始弯曲含弹性预载。每个弯曲轴限位由最大弯角与局部段长/最小弯曲半径共同限制；这是分轴离散限位，不是严格三维最小曲率保证。当前不支持空间曲线、曲线无应力自然态、连续分布阻尼、扁平/分叉/真实多芯内部模型；不要静默近似。

自接触、相邻过滤、端部一段对连接刚体所有叶 collider 的过滤来自保存配置。`body.apply_to_endpoints` 仅显式应用迭代数和预测式 CCD，不更改连接刚体的质量、模式、阻尼。多个链请求不同场景策略或对同 actor 请求冲突设置必须报错。CPU 刚体链与 GPU FEM 场景策略冲突时拒绝混合导出。

细分使胶囊半球帽跨越不止相邻一段时，可显式启用 `collision.filter_material_neighbors=true`：仅过滤材料区间间隙不大于两半径之和的局部邻段，保留远弧长折回接触。省略/false 保持 V6 原过滤对；不要用永久关闭全部接触冒充修复。

正常导出后调用 `validate`，检查 Capsule、弧长质量、惯量、关节锚点/自然框架、EI/GJ 驱动、阻尼、CCD、过滤和数量。报告 `physics.rigid_chains` 保留各段和端部对应关系。静态通过不等于 PhysX 仿真通过；轨迹、受控载荷和物理标定另需运行记录。未经实物测试的参数只能说明为用户指定或估算，不能宣称精确再现真实线缆。

历史0.1.6rc1结果：V6 从零流程和原 5 秒门槛通过，13 个通用案例结构稳定性通过，但受控 N/2N/4N 的 EI/GJ 一致性和 GJ 理论精度未通过，原记录保留。0.1.6rc2导出明确的TGS外力每子步策略，并严格重开校验；仅此策略不能消除角度误差。不要以“更硬响应更小”或一帧更直替代定量验收；当前公共实现尚不能宣称通用工程精度通过。步频/迭代收敛、两端半段离散柔度、材料轴相对转角和实际串联弹簧参考须分别核查；原瞬时速度失败不得用固定窗平均运动通过覆盖。名义k*angle不是native drive冲量。
