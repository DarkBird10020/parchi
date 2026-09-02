# Results (1000 synthetic agent transactions)

Intent provider: `heuristic`

| Approach | Catches violations | Blocks good customers | Cost of the mistakes |
| --- | --- | --- | --- |
| Allow everything | 0/280 (0%) | 0 customers | Rs 0.00 lost to false blocks · Rs 1,783,157.14 paid out on violations |
| Block all agent traffic | 280/280 (100%) | 720 customers | Rs 3,373,331.46 lost to false blocks · Rs 0.00 paid out on violations |
| Rules only (day 2) | 235/280 (84%) | 0 customers | Rs 0.00 lost to false blocks · Rs 219,907.65 paid out on violations |
| Parchi (rules + one model call) | 280/280 (100%) | 0 customers | Rs 0.00 lost to false blocks · Rs 0.00 paid out on violations |
