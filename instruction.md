# 长任务中断后的积分复核

实验排程每天夜里计算响应曲线积分，机器会按配额暂停并在次日续算。现有 `/app/adaptive_quadrature_resume.py` 的误差控制不稳，也不保存细分现场。

## 输入

程序读取 UTF-8 文本，内容必须是一个 JSON 对象。顶层键如下：

```text
problem_id
interval
coefficients
tolerance
refinements_per_run
```

`problem_id` 记录本次积分批次，可见字符数限定在 2 至 48；前后空格和控制字符均不接受。积分区间由 `interval` 给出，是两个有限端点且左端必须小于右端。`coefficients` 是 1 到 12 个有限系数，按常数项到最高次项排列，定义多项式 `p(x)`。误差上限 `tolerance` 介于 1e-12 与 1e-2 之间。`refinements_per_run` 是 1 到 10000 的整数。布尔值不算数字或整数。

调用方式：

```bash
python3 /app/adaptive_quadrature_resume.py <问题文件> <积分凭证> <检查点>
```

## 自适应积分

使用复合 Simpson 规则。每个完整区间的误差估计为 `|L+R-W|/15`，其中 W 是该区间一步 Simpson 值，L、R 是左右半区间的一步 Simpson 值之和。

一次调用最多细分 `refinements_per_run` 次。当所有活动区间的误差估计之和不超过 `tolerance` 时完成，否则写 `paused` 检查点。下一调用从同一检查点继续。一次完成与分段完成最终得到的积分值、有效区间集合顺序和检查点必须逐字节一致。

## 输出

积分凭证是单行紧凑 JSON，末尾一个 LF。完成态键顺序为 `problem_id`、`status`、`integral`、`active_intervals`；`active_intervals` 是完成后按左端点升序排列的区间数。暂停态键顺序为 `problem_id`、`status`、`partial_integral`、`pending_intervals`、`refinements_done`。

检查点对象顶层键如下：

```text
input_sha256
segments
integral
refinements_done
```

`input_sha256` 标识数学问题本身；计算时排除分片预算。检查点摘要、结构、段状态或累计量对不上时按损坏拒收。

运行完成时，不向标准输出或标准错误写任何字节。同一输入完成后重跑，积分凭证与检查点保持原字节。发布时两个目标先写好临时文件再替换；任一失败都恢复旧字节并清理临时候选。

## 拒收

若积分记录字段不全、类型错或参数越界，程序以 1 退出，标准错误写 `运行拒收: BAD_INPUT`。

若续算状态损坏，返回码为 1，标准错误只含 `运行拒收: BAD_CHECKPOINT`。

如果结果文件或检查点无法创建、替换，退出码为 1，并在标准错误单独写出 `运行拒收: IO_FAILURE`。

错误输出中不应出现异常堆栈。
