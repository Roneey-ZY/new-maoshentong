# 第三方公开合成采购数据验证

## 来源、授权和定位

采用 [Database Sample 的采购订单与收货数据集](https://databasesample.com/datasets/procurement-purchase-orders-and-receipts-dataset)，版本 1，数据集页面标注 CC0-1.0。原始 8 张 CSV、492 行全部下载，逐文件与发布方公布的 SHA256 和行数核对一致。没有购买 Mobiusi 或其他付费数据，没有调用付费模型。

本轮是“增加第三方公开合成数据验证”，不是企业实证，也不是第三方独立测评。来源独立于我们此前自行生成的模拟单证，但规则参考答案由项目自己定义，不是数据发布方提供的异常标签。

原始来源说明、下载 URL、版本、日期、授权和校验值见 [source.json](data/external/databasesample/procurement-v1/source.json)。原始 CSV 按字节保留在 `data/external/databasesample/procurement-v1/raw/`，Git 属性避免换行转换破坏哈希。

## 先试接入，再冻结划分

先按采购订单 ID 选择前 5 笔试接入，结果：208 个转换字段、233 个原始字段位置核对，未发现字段或来源差异。[试接入记录](data/external/databasesample/procurement-v1/pilot/evaluation.json)

确认字段及业务口径可用后，按固定哈希排序划分：

- 开发组 12 笔，包含这 5 笔试接入，不能把这 5 笔算作未见样本。
- 留出组 24 笔。
- 以采购订单 ID 为划分单位，同一订单全部采购行、收货和发票在同一组。
- [protocol.json](data/external/databasesample/procurement-v1/protocol.json) 固定分组、政策版本、来源元数据哈希和引擎哈希。输出和协议不允许原地覆盖。

## 与跨境单证审核的边界

这是采购与收货记录，不是跨境装箱单。单独实现为“公开采购数据验证”，没有把 goods_receipts 改名为 Packing List，也没有导入私人审核任务数据库。用户原有采购订单、商业发票、装箱单流程仍单独评价。

| 能力 | 本轮处理 |
|---|---|
| 采购与发票单价 | 同一采购行精确比较，不设隐含容差；按来源说明的 USD 口径解释，不伪造逐单币种 |
| 订单、累计收货、累计开票数量 | Decimal 计算；按全量快照比较，不能推断发票开具当日的到货情况 |
| 分批 | 少于订单可为分批，列为业务状态说明；超过数量给出业务差异核对提醒 |
| 没有记录 | 保留无法核验，不将未提供的记录直接当作实际数量零 |
| 多张发票 | 保留每张独立编号并检查累计量，不直接判为重复或欺诈 |
| 收货品况 | damaged/wrong_item 需要人工复核，不推断已验收合格数量 |
| 发票行金额、总金额算术 | 来源没有独立金额，本轮不评估；不补造乘积再自证正确 |
| 净重、毛重、包装件数 | 非装箱单且没有这些字段，不适用，不补造 |
| 币种、合格验收数量及付款金额 | 缺少逐单依据，无法核验 |

关联使用 `purchase_order_id → po_lines.id → po_line_id`，不按文件行号或仅按 SKU 配对。同一 SKU 在不同采购行出现时保持分开。商品名、SKU、单位从商品主表查询，明确标记为 `master_lookup`；直接取值标为 `direct`。每项保留原文件、起止行、原记录编号、原列和原值。

生成器辅助字段 `qty_ratio`、`billing_drift`、`received_share`、`negotiated_factor` 以及付款状态不用于判断对错。原始表保留这些字段，但业务转换输出不携带它们作为答案提示。

## 实测结果

核心规则使用 Decimal；另一份参考实现独立读取原始 CSV，使用 Fraction 复核。参考实现没有调用生产审核函数。对照故意清空审核结果的测试可检出缺失核验项，避免将“都没有结果”误判为通过。

| 分组 | 订单数 | 与参考口径一致 | 转换字段核对 | 来源位置核对 | 业务差异项 | 人工复核项 | 状态说明项 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 开发 | 12 | 12 | 596 | 667 | 67 | 51 | 29 |
| 留出 | 24 | 24 | 976 | 1081 | 110 | 99 | 42 |
| 合计 | 36 | 36 | 1572 | 1748 | 177 | 150 | 71 |

转换字段和来源位置均未发现差异；91 条采购行、89 条收货、87 条发票全部保留。177 项业务差异不是 177 笔错误交易，也不是欺诈标签。150 项包含信息不足和品况复核。全部结果与项目参考实现一致，**不等于真实企业准确率或模型准确率达到 100%**。

原始结果见 [evaluation.json](data/external/databasesample/procurement-v1/evaluation/evaluation.json)，说明见 [REPORT.md](data/external/databasesample/procurement-v1/evaluation/REPORT.md)。每笔订单另存转换 JSON、规则证据、评价结果和 Excel。模型调用为 0，真人复核为 0，没有测量真人操作时间。

本地完整测试 **73 通过、1 PostgreSQL 专项跳过，27.57 秒**；[原始 JUnit](data/external/databasesample/procurement-v1/test-results.xml)。覆盖校验失败阻断、外键错误、不丢失子记录、同 SKU 不同采购行、分批、缺失、累计开票、金额精度、独立参考发现错误实现、Excel 公式保护和 Streamlit 实际规则执行、四个下载入口。

提交 `f48d194` 的 [GitHub Actions](https://github.com/Roneey-ZY/new-maoshentong/actions/runs/45008786437) 已成功完成 tests、postgres、container 三个任务；Linux 流程重新执行36笔采购验证并保存结果。该记录不代替新增公开页面的使用者验收。

## 页面验收

登录新贸审通，展开底部“公开采购数据验证 · 订单、收货与开票”：

1. 默认选择 5 笔试接入订单，也可切换开发组或留出组。
2. 选择一笔采购订单，点击“执行本订单采购规则核对”。
3. 查看实际执行的四个规则函数、累计数量、业务差异、人工复核、状态说明及源文件行位置。
4. 下载采购核对 Excel、本订单 JSON、全部原始公开数据与协议、整批验证结果。

页面不自动调用模型，不将固定规则函数记录冒充模型自主调用。此入口使用公开合成数据，结果为独立预览，保存原有私人任务仍使用原任务保存功能。

## 复现

```powershell
python -m pip install -r requirements-dev.txt
python -m scripts.evaluate_procurement --phase evaluate --output procurement-repeat-results
python -m pytest tests/test_procurement.py -q
```

原始数据已经随 CC0 源码包提供，复现无需重新下载。需要重新取得源文件时，用 `scripts.fetch_procurement_dataset --output 新目录`，不要覆盖版本记录；试接入和冻结命令分别为 `scripts.evaluate_procurement --phase pilot` 与 `--phase freeze`，已有目录会拒绝覆盖。引擎改变后必须归档并创建新的实验版本，不能沿用旧冻结协议宣称同一次结果。

## 比赛材料建议表述

> 在自建单证测试基础上，增加 Database Sample 第三方公开合成采购数据验证。完成 36 张订单、91 条采购行及关联收货、发票记录的转换、溯源与采购快照核对，并按订单划分开发和留出数据。该实验验证字段转换与项目规则一致性，不替代真实跨境单证或企业实证；装箱重量和独立金额验证另行报告。
