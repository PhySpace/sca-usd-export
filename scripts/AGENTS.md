# 安装与验证脚本约定

先阅读仓库 README 和脚本参数。保持 Windows 的 JSON/Base64 参数包装器。
安装和更新只作用于调用者明确选择的 add-on/runtime；隔离验收必须隔离 USERPROFILE 和 SCA 状态。
不隐式写全局配置、不切换渠道、不删除旧 runtime。所有产物和缓存位于仓库外。
保留原有 Git 来源、许可证和其他 Agent 的修改；不自动发布或推送。
