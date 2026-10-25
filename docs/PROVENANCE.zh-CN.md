# 来源与维护范围

本维护源从已保留的 cad-deformable-0.1.5rc1/sca-usd-export 候选复制，原 54 文件清单摘要为 `12087caadac7e16e101cfb2a76d37fd2f76168e55bc630bb403bae279f175098`。该摘要描述整理前源码，不是当前版本 Git 提交号。

原仓库来源为 https://github.com/wwlloo/sca-usd-export.git ，保留其 `9fa527b4395b4ea7b46e4772305da5196fc17a74` 历史关系；维护源基线提交为 `a6ac348ebf7e97508fc8a773800178f8365e4e21`。远程配置不能独立证明操作者拥有该仓库。没有自动推送、发布或改变远程归属。

导出器更早改编自 SimpleCADAPI extensions/usd_export/，Apache-2.0 原许可和 NOTICE 原文保留。SCA 核心及第三方二进制不随本仓打包。暂停的 0.2 工作树、0.1.3/0.1.4 同步声明支线保持各自身份。

0.1.6rc1 实质新增是消费物理插件 schema 1.4 rigid_joint_chain，直接导出多段刚体/D6 关节及端部/碰撞配置，补充 capsule/角驱动单位转换和独立重开验证。几何、轨迹、原生步进与实测标定不属于导出插件。隔离安装、实际 CAD 从零构建及原生行为验证，以当前版本对应结果记录为依据；静态重开不自动构成动态通过。
