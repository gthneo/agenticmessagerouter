<!--
agentic-contracts PR 模板 —— 治理铁律见 GOVERNANCE.md（先读）。
单账号 = 无身份分权 → 机器闸(CI) + agent 级分权(作者 agent ≠ 审查 agent) 一个都不能少。
-->

## 改了什么 / 为什么 / 影响哪些消费方

<!-- 一句话说清：改了哪份契约、为什么、AMR/AMP/fullwechat/PowerData 哪些消费方受影响 -->

## 治理清单（逐条勾，缺一不可）

- [ ] **已对照 `00-CONSTITUTION.md`**——本变更**无旁路人的路径**（对外动作仍走人审/否决窗，自动挡不自升，结果回交给人）。
- [ ] **改的是契约本体，非本地补丁**——口径变更落在本仓 spec，**没有**在某个消费方仓里硬编码私自解释绕过。
- [ ] **跑了 conformance**——`python3 scripts/conformance.py` 本地绿（CI 的 `conformance` check 也必须绿）。
- [ ] **独立 agent 对抗审过（贴结论）**——由一个**非作者**的 fresh Claude Code agent 对着 `00-CONSTITUTION.md` + 相关契约 + conformance 做对抗式审查，结论贴在下方。
- [ ] **破坏性变更已 bump 版本**——若是 major（不兼容），已**另开新文件 + bump schema major**（见 `VERSIONING.md`）；minor 保证向后兼容。

## 独立 agent 对抗审查结论（必填）

> 审查 agent ≠ 作者 agent。这是单账号下重建分权的唯一办法。把审查 agent 的逐条核查结论贴这里：
> 「有没有旁路人 / 有没有偷改语义 / 有没有过 conformance / 是否违宪」。

```
（粘贴独立审查 agent 的结论）
```

## merge 前

- [ ] CI 全绿（含 `conformance`）。
- [ ] **由人按下 merge**（HITL 最后一闸）——不做全自动合并。
