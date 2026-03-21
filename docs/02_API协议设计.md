
# API 协议设计

基础路径

/api/v1

核心接口

POST /api/v1/resources/search-and-deliver
GET /api/v1/tasks/{task_id}
GET /api/v1/health

统一返回结构

{
 success: true,
 code: "OK",
 message: "success",
 request_id: "...",
 data: {}
}
