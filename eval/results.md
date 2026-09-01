# Results (1000 synthetic agent transactions)

Intent provider: `heuristic`

| Approach | Catches violations | Blocks good customers | Cost of the mistakes |
| --- | --- | --- | --- |
| Allow everything | 0/260 (0%) | 0 customers | Rs 0.00 lost to false blocks · Rs 1,705,171.05 paid out on violations |
| Block all agent traffic | 260/260 (100%) | 740 customers | Rs 3,443,816.09 lost to false blocks · Rs 0.00 paid out on violations |
| Rules only (day 2) | 235/260 (90%) | 0 customers | Rs 0.00 lost to false blocks · Rs 120,785.18 paid out on violations |
| Parchi (rules + one model call) | 260/260 (100%) | 0 customers | Rs 0.00 lost to false blocks · Rs 0.00 paid out on violations |
