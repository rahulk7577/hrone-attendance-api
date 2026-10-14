# Starter Code Review you found in the starter `app/main.py` (helpers and endpoints). For each one:
| # | Where | What is wrong | How you'd notice it | How you fixed it |
|---|---|---|---|---|
| 1 | `list_employees()` | `total` counted attendance records instead of employees. | `GET /employees` could show the number of attendance records as the employee total. | Changed the count to `db.employees.count_documents(q)`. |

### Checked but not defects
- `health()` already checks the MongoDB connection.
- `list_employees()` already has correct pagination, sorting, and page limits.