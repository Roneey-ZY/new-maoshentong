# B 阶段第四步：冻结合成模板评估

本轮准备 24 笔新构造的模拟交易，8 笔开发参照、16 笔模板留出。样本覆盖 CSV、XLSX、文本 PDF、商品行换序、中英文陌生表头、未识别的订单号标签、重复 SKU、未知单位、真实缺失重量和订单关联失败。

**全部为合成数据。** 样本由同一开发者设计，不是第三方独立评估，也不是企业实证。没有获得企业实际单证或开展真人操作试验，本轮不能填补这两项证据空缺。

## 实验控制

- 样本、标准答案和“预设正确映射模拟人工确认”动作在测量前生成；生成器不调用生产解析器或审核规则生成答案。
- `manifest.json` 保存每个文件和评估引擎的 SHA256。禁止覆盖已有冻结目录；引擎改变时旧实验脚本拒绝假装复现同一实验。
- 留出标签表示本轮没有根据测量结果调整模板读取逻辑；六种模板家族的数量很少，不具备统计代表性。
- Agent 组只接收文件中的候选内容，不读取标准答案或预设确认文件。测试程序模拟应用通过现有证据校验的模型建议，**不会先比对答案、只挑正确建议**。
- 预设映射组从冻结的确认文件读取正确映射，因此是有条件的参考组，不能称作大模型独立达到的结果。
- 所有真人确认次数为 0，真人耗时为空。记录的是程序耗时，模型组还含网络等待。重跑模型具有随机性。

## 首次实测

2026-11-22，本地 Python 3.12、`deepseek-flash`。完整原始记录：

- [冻结样本与答案](data/independent_validation/v1/manifest.json)
- [离线对照记录](data/independent_results/v1-offline/comparison.json)（模型组明确跳过）
- [三组真实对照报告](data/independent_results/v1-live/REPORT.md)
- [逐笔字段、发现、建议、工具事件和请求用量](data/independent_results/v1-live/comparison.json)

| 处理方式 | 开发参照通过 | 模板留出通过 | 全部通过 | 留出字段正确率 | 留出异常召回率 |
|---|---:|---:|---:|---:|---:|
| 原有解析与规则 | 8/8 | 4/16 | 12/24 | 92.5% | 25.0% |
| 真实 Agent 建议＋程序模拟确认 | 8/8 | 9/16 | 17/24 | 95.9% | 41.7% |
| 预设正确映射模拟人工确认 | 8/8 | 16/16 | 24/24 | 100% | 100% |

结果表明：在这批固定模板上，辅助建议增加了可完整处理的案例，但可靠性仍不够。不能将 17/24 描述成企业普遍成功率，不能用参考组 24/24 替代真实 Agent 成绩。模型组平均程序耗时更长，没有获得速度优势。

模型组实际发出 61 次请求；API 成功返回用量，但其中 6 笔交易未通过 SDK 的模型输出校验（ModelBehaviorError）。这与网络请求失败不同。已知输入 104,557、输出 10,090 token，其中缓存输入 87,936 token。未取得供应商账单，实际收费留空。

## 失败保留与发现

- 6 笔交易发生结构化结果错误，保留实际请求和已执行工具，应用继续提供人工复核入口，不伪造成功。
- 另有 1 笔重复 SKU 案例，模型把一条商品数据行建议为表头。程序模拟接受后少读了一行，导致重复提醒消失；该笔被评估判为失败。这是不能盲目自动接受模型建议的直接证据。
- 首次记录保留在原目录，后续修复不覆盖其结果。修复后使用已经看过的样本复测，应称作回归验证，不能继续称作未见模板评估。

## 复现

首次实验的完整代码和原始字节归档在提交 **`f3f8161`**。当前版本已增加表头防丢行保护，不能将当前版本当作原实验引擎。请在独立工作目录检出原实验代码后复现：

```powershell
git worktree add ../tradecheck-evaluation-v1 f3f8161
cd ../tradecheck-evaluation-v1
python -m pip install -r requirements-dev.txt
python -m scripts.evaluate_independent --dataset data/independent_validation/v1 --output data/independent_results/repeat-offline
# 服务端已经配置模型密钥时才执行真实模型组：
python -m scripts.evaluate_independent --dataset data/independent_validation/v1 --output data/independent_results/repeat-live --live-ai
```

输出目录必须是新的目录。实时模型组会产生模型调用消耗，不在网页刷新时自动执行。网页仅读取已发布的实验记录并提供下载。

在当前代码目录可运行 `python -m scripts.verify_frozen_archive` 校验 Git 归档中所有原始样本和引擎文件的字节哈希，无需发出模型请求。Git 属性固定冻结输入和引擎源码的字节，避免 Windows/Linux 换行转换破坏哈希。

## 已修复的防丢行问题

当前版本不再向诊断模型提供位于已有商品行中的候选“表头”，也拒绝应用这样的确认。即使绕过候选筛选，重解析结果也必须保留原有商品行的位置和数量，不能通过少读商品行使异常消失。正常的字段预览修正和陌生表头确认仍受原有流程保护。

对首次失败的原始错误建议进行回放：[安全回归记录](data/independent_results/postfix-safety-regression.json)。修复后拒绝该建议，保留全部 4 个单证商品行（其中发票 2 行）和重复 SKU 待复核提醒，没有新增模型调用。**这是已见失败的回归测试，不是新的独立样本成绩。**

完整本地测试 64 通过、1 跳过，25.86 秒；[JUnit 原始记录](data/independent-step-test-results.xml)。提交 `0c08f6b` 的 [GitHub CI](https://github.com/Roneey-ZY/new-maoshentong/actions/runs/45004032718) 三个任务均成功，包括真实 PostgreSQL、Linux 测试与容器启动；Linux 流程也校验了归档文件哈希并执行失败建议回放。

新一轮冻结样本可用 `python -m scripts.generate_independent_validation --output data/independent_validation/v2` 生成；若仍用相同固定种子和模板，则是重复实验，不应当声称换成了全新独立样本。

## 尚需补充

后续仍需经授权的企业脱敏单证、合法公开模板及真人复核测试；分别记录来源、授权、操作者、实际确认次数和实际用时。原有 60 组基准用于回归，新 24 组用于本轮冻结模板对照，二者不能混在一起选择有利数字。
