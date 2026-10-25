# Agent 决策、独立导出与安装身份

当前候选配套版本为 `sca-physics==0.1.8rc1`、`sca-usd-export==0.1.8rc2`。Schema1.7增加严格输入来源与可复核派生记录；旧1.0–1.6读取时保持原值和来源，不升级、不追认来源、不重选段。Schema1.8新增普通关节声明，不改变此决策流程；精确环境、所测旧包及升级边界见[兼容矩阵](compatibility-0.1.8rc2.zh-CN.md)。

Agent判断构造、用途、模型适用性及资料，提交有来源的参数或完整声明。插件不调用大模型，不从未知描述猜普通电线；匹配已有预设时必须明确类别，自定义适用圆杆使用`set-rigid-chain`。已提交的有效参数不由预设覆盖，预设只补缺。未注明来源的输入拒绝；逐值来源优先于本次组/声明/根来源，程序默认值自行标`default`。

`derived`保存实际输入及受支持公式，并重新计算核对；它证明计算关系，不证明资料真实性或材料标定。均质圆杆的`rho*A`、`E*I`、`G*J`不能直接套到复合电线。配套physics公开`circle_rod`换算函数和`examples/agent_decisions/run_task.py`演示完整流程；初始估计、任务预算和验收条件分别记录。

## 只导出选定的最终包

```powershell
$FinalSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $FinalPackage).Hash.ToLowerInvariant()
& $UsdPython -I -B -m sca_usd_export export $FinalPackage $NewUsda `
  --require-physics --expected-input-sha256 $FinalSha
if ($LASTEXITCODE) { throw 'Final package export failed' }
& $UsdPython -I -B -m sca_usd_export validate $NewUsda
if ($LASTEXITCODE) { throw 'USD reopen validation failed' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $FinalPackage).Hash.ToLowerInvariant() -ne $FinalSha) {
  throw 'Final package bytes changed'
}
```

Python `export_package`同样提供`expected_input_sha256`、`require_physics`参数。输入只读取一次，解码被核验的同一份字节；报告保存`input_package_sha256`与`physics_metadata_sha256`。旧调用保持兼容；新任务应启用两项保护。导出不接收原建模变量，也不需要物理声明JSON。源包和输出路径不可互相覆盖。

## 构建、安装与更新

`scripts/pair_deploy.py`提供成对部署入口；先查看`--help`。显式传入宿主/基础解释器、两源码、home、skills和新证据目录。`stage --apply`在隔离用户配置内安装、从payload构建wheel、provision并检查独立runtime；不切真实默认。提供物理仓库或其wheel，不能借用另一venv或假设未发布的配套包已在公网索引。

安装记录包含源码/业务文件、wheel和真实模块哈希。每次正式dispatch在导入业务包前核对payload与runtime；`addon update`仅更新payload而未更新runtime时会拒绝，并要求同一安装的provision。版本号相同不代表内容相同；检查成功才执行任务。

真实默认激活应在完整案例通过后进行，保留配置、registry、全局技能和旧runtime的恢复资料。SCA2.2.5先使用旧registry保存的技能目标再处理update参数，因此须先将仍有职责的旧registry从全局目标改绑私有目录。历史显式回退须带降级意图与预期当前身份；不要盲目卸载共享全局目标的旧渠道。归档快照不随更新改写。

隔离验证记录不能代替默认入口复核，也不能代替GitHub下载安装或原生行为验收。运行结果解释与10m/s诊断线见[任务原生检查](runtime-task-validation.zh-CN.md)。
