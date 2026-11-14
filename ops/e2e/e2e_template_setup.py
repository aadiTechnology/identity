"""Gives the template product a plan in the local Identity through the admin API (what an administrator does in the console). Run after e2e_template.py prepare."""
import httpx

c = httpx.Client(base_url="http://localhost:8010", follow_redirects=False)
c.get("/login")
c.post("/login", data={"email": "platform@aadi-demo.example.com", "password": "Platform-Demo-1", "next": "/", "csrf": c.cookies.get("id_csrf")})
h = {"x-csrf-token": c.cookies.get("id_csrf")}
a = c.post("/admin/api/plans", json={"key": "template-pro", "product_key": "template-demo", "name": "Template Pro", "features": ["campaigns.basic"], "limits": {"campaigns": 10}}, headers=h)
b = c.post("/admin/api/tenants/5ca1ab1e-aad1-4e57-8000-000000000001/plan", json={"product": "template-demo", "plan": "template-pro"}, headers=h)
print("plan", a.status_code, "assigned", b.status_code)
