# 普通关节执行器消费合同

从配套 sca-physics 保存的 schema1.8 最终产品包读取 `joints[id]`，不得由名称或外观推断关节、轴、参数或电机规格。`motion` 是 free/limited，limited配 lower_limit/upper_limit；旋转rad、移动m。max_velocity为rad/s或m/s，friction为单位1的PhysxJointAPI系数，两者要求同一有效articulation。

drive完整字段为mode（off/position/velocity）、type（force）、target_position、target_velocity、stiffness、damping、max_force，各值必须携单位和真实来源。position要求K>0，velocity要求K=0且D>0。off只保留旧被动阻尼。angular K/D分别N*m/rad、N*m*s/rad，linear为N/m、N*s/m；上限为N*m或N。

单轴DriveAPI共用通道：Dtotal=Dp+Da，有效速度目标=Da/Dtotal*vtarget，K和位置目标保持。主动max_force约束整个组合，不单独限制主动项。目标/速度/限位角度由rad乘180/pi；角K/D由每rad乘pi/180成每degree。米到厘米舞台角K/D、力矩、惯量乘10000，线长度/速度/力上限乘100，线K/D不变。

assembly.articulation要求enabled布尔、root_node_id精确实例/根ID、fixed_base布尔。固定根原有ground/fixed簇导出为anchored动态root actor+world FixedJoint并在该joint应用RootAPI；保留语义，报告区分可动actor与anchored actor。根非原点位置/旋转保留。普通关节非零初态来自求解后connector相对frame，写PhysicsJointStateAPI。只支持单一相连无环ordinary关节树；拒绝无法保持语义的闭环、分离链、kinematic与多世界连接。

顶层couplings[id]的mimic只支持同一articulation、limited旋转reference→limited移动follower，g单位m/rad、offset为m。方程qf+g*qr+offset=0，USD gearing=g*pi/180再按舞台长度换算，offset同长度换算。follower无DriveAPI且D=0；真实初态必须满足方程。它是理想坐标约束，不模拟齿隙、齿面碰撞、弹性或损耗。不能用给两个目标或改Transform冒充耦合。

```python
from sca_usd_export import update_drive_targets
update_drive_targets(stage, "joint/arm6/J1", target_position=0.2617993877991494)
```

运行时SI helper仅更新原有Drive目标，缺少API、模式off、错误ID、非法值、越界位置均拒绝。不能用运行层补建驱动后把源导出判为PASS。完整API和本机107.3官方依据见安装payload `docs/joint-actuators.zh-CN.md`；另保存实际Isaac拓扑/运动/碰撞证据。
