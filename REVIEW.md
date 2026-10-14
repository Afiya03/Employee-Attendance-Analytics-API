# Code Review

## Defects Identified and Fixes Applied

| # | Area | Defect | How to Identify | Fix Applied |
|---|---|---|---|---|
| 1 | API responses | MongoDB `_id` should not appear in API responses. | Inspect employee and attendance responses. | Excluded `_id` from returned documents where implemented. |
| 2 | Timestamp handling | The API uses epoch milliseconds while MongoDB stores datetime values. | Test punch-in and punch-out requests and responses. | Added timestamp conversion between API and database formats. |
| 3 | Attendance pagination | Pagination must return the filtered total and requested page. | Test page and page-size parameters. | Added filtered record counts and pagination using skip and limit. |
| 4 | Late calculation | Late arrival must follow the specified 10-minute grace rule. | Test arrival at, before, and after the grace boundary. | Updated the late-minute calculation. |
| 5 | Work-hour calculation | Work hours require consistent rounding. | Test durations that produce decimal-hour values. | Used decimal half-up rounding. |
| 6 | Overtime calculation | Overtime is counted in whole minutes and applies only from 30 minutes. | Test punch-out times around the threshold and for overnight shifts. | Updated the overtime calculation; overnight cases still require verification. |
| 7 | Department trend | Trend output must include dates without attendance records. | Query a date range containing dates without logs. | Added MongoDB `$densify` and moving-average processing. |
| 8 | Query performance | Analytics queries need suitable indexes and execution-plan checks. | Inspect execution statistics for index scans. | Added and checked indexes for employee and attendance lookups. |
| 9 | Dependencies | The code imported scikit-learn, but it was absent from the dependency list. | Install dependencies in a clean environment. | Added scikit-learn to `requirements.txt`; confirm it is required by the final code. |

## Items Requiring Further Verification

- Overnight-shift date assignment and overtime calculations.
- Monday-to-Friday working-day calculations, including month boundaries.
- Input validation for employee fields, statuses, and pagination limits.
- All analytics against the complete assignment rules and larger datasets.
- Automated tests for boundary conditions and concurrent attendance updates.

## Reviewed Design Decisions

- MongoDB stores timestamps as datetime values; the API exposes epoch milliseconds.
- Attendance records use employee code and calendar date as their natural key.
- Attendance regularization records an audit history.
- MongoDB indexes support frequent employee and attendance queries.

This review records identified changes and remaining verification work. It does not claim that every assignment requirement has passed automated testing.
