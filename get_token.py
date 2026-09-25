from fyers_apiv3 import fyersModel

app_id = "B87U7R9BRA-200"                # your App ID
secret_key = "l25ijVstjY"      # ← from dashboard App details (Secret Key)
redirect_uri = "https://trade.fyers.in/api-login/redirect-uri/index.html"

auth_code = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHBfaWQiOiJCODdVN1I5QlJBIiwidXVpZCI6IjVkM2MwNjA5NTVmMDRlNTA4ZjZmOGFjMTY1OTUyY2NhIiwiaXBBZGRyIjoiIiwibm9uY2UiOiIiLCJzY29wZSI6IiIsImRpc3BsYXlfbmFtZSI6IllBMTUzMjUiLCJvbXMiOiJLMSIsImhzbV9rZXkiOiI3YTUwYzE3ZGFlZWQ5Zjc4OTA5OTM2NGY4YjU4YWY1NTIwNzA3N2UwOGFiYmYwODllOTMwNmMxMiIsImlzRGRwaUVuYWJsZWQiOiJOIiwiaXNNdGZFbmFibGVkIjoiTiIsImF1ZCI6IltcImQ6MVwiLFwiZDoyXCIsXCJ4OjBcIixcIng6MVwiLFwieDoyXCJdIiwiZXhwIjoxNzkwMzQ4NDIzLCJpYXQiOjE3OTAzMTg0MjMsImlzcyI6ImFwaS5sb2dpbi5meWVycy5pbiIsIm5iZiI6MTc5MDMxODQyMywic3ViIjoiYXV0aF9jb2RlIn0.G7S1DD96xMazUr3BKFWgdNoc1NC0inFRbGJsh8h1CWE"  # ← the eyJ... code you just got

session = fyersModel.SessionModel(
    client_id=app_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type="code",
    grant_type="authorization_code"
)

session.set_token(auth_code)
response = session.generate_token()

print(response)
