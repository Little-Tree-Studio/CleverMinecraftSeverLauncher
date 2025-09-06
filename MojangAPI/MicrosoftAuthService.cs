using System;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace MojangAPI
{
    public class MicrosoftAuthService
    {
        private const string ClientId = "00000000402b5328";
        private const string RedirectUri = "https://login.live.com/oauth20_desktop.srf";
        private const string Scope = "service::user.auth.xboxlive.com::MBI_SSL";

        public string GetAuthorizationUrl()
        {
            return $"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_id={ClientId}&response_type=code&redirect_uri={Uri.EscapeDataString(RedirectUri)}&response_mode=query&scope={Uri.EscapeDataString(Scope)}";
        }

        public async Task<string> GetMicrosoftTokenAsync(string authorizationCode)
        {
            var content = $"client_id={ClientId}&code={authorizationCode}&grant_type=authorization_code&redirect_uri={Uri.EscapeDataString(RedirectUri)}&scope={Uri.EscapeDataString(Scope)}";
            var response = await HttpClientHelper.PostAsync("https://login.live.com/oauth20_token.srf", content, "application/x-www-form-urlencoded");
            dynamic jsonResponse = JsonConvert.DeserializeObject(response);
            return jsonResponse.access_token;
        }

        public async Task<string> GetXboxLiveTokenAsync(string microsoftToken)
        {
            var requestBody = new
            {
                Properties = new
                {
                    AuthMethod = "RPS",
                    SiteName = "user.auth.xboxlive.com",
                    RpsTicket = microsoftToken
                },
                RelyingParty = "http://auth.xboxlive.com",
                TokenType = "JWT"
            };

            var response = await HttpClientHelper.PostAsync("https://user.auth.xboxlive.com/user/authenticate", JsonConvert.SerializeObject(requestBody));
            dynamic jsonResponse = JsonConvert.DeserializeObject(response);
            return jsonResponse.Token;
        }

        public async Task<string> GetXSTSTokenAsync(string xboxLiveToken)
        {
            var requestBody = new
            {
                Properties = new
                {
                    SandboxId = "RETAIL",
                    UserTokens = new[] { xboxLiveToken }
                },
                RelyingParty = "rp://api.minecraftservices.com/",
                TokenType = "JWT"
            };

            var response = await HttpClientHelper.PostAsync("https://xsts.auth.xboxlive.com/xsts/authorize", JsonConvert.SerializeObject(requestBody));
            dynamic jsonResponse = JsonConvert.DeserializeObject(response);
            return jsonResponse.Token;
        }

        public async Task<string> GetMinecraftTokenAsync(string xstsToken, string userHash)
        {
            var requestBody = new
            {
                identityToken = $"XBL3.0 x={userHash};{xstsToken}"
            };

            var response = await HttpClientHelper.PostAsync("https://api.minecraftservices.com/authentication/login_with_xbox", JsonConvert.SerializeObject(requestBody));
            dynamic jsonResponse = JsonConvert.DeserializeObject(response);
            return jsonResponse.access_token;
        }
    }
}