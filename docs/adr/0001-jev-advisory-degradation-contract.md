# Jev 判断点采用 advisory-only、逐点 opt-in、每份限次的降级契约

jev（TypeSafe System One）作为外部判断 API 接入攻略生成流程的两个判断点（素材可信度、行程合理性）。决策：jev 判定仅产生 advisory 告警，blocking 权力只属于硬规则引擎；每个判断点默认关闭、独立 env 开启（per-point opt-in）；每份攻略限制 jev 调用次数（默认 20，env 可调）。这样定是因为 jev 是外部计费 API 且可用性不由我们控制——fail-closed（jev 挂了就阻断生成）会让攻略生成被单点依赖劫持；key-即-全开则成本不可控（参见 oh-my-claudecode #4056 的教训：一个 key 本身不启用任何东西）。
