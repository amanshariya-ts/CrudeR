from fyers_apiv3 import fyersModel

client_id = "B87U7R9BRA-200"      
secret_key = "l25ijVstjY" 
redirect_uri = "https://trade.fyers.in/api-login/redirect-uri/index.html" # Must match Dashboard

session = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type="code",
    state="sample_state"
)

auth_url = session.generate_authcode()
print("Open this URL in your browser:", auth_url)