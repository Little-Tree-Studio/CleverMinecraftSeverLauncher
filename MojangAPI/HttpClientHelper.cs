using System;
using System.Net.Http;
using System.Threading.Tasks;

namespace MojangAPI
{
    public static class HttpClientHelper
    {
        private static readonly HttpClient _httpClient = new HttpClient();

        public static async Task<string> PostAsync(string url, string content, string contentType = "application/json")
        {
            var requestContent = new StringContent(content);
            requestContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(contentType);
            var response = await _httpClient.PostAsync(url, requestContent);
            return await response.Content.ReadAsStringAsync();
        }

        public static async Task<string> GetAsync(string url)
        {
            var response = await _httpClient.GetAsync(url);
            return await response.Content.ReadAsStringAsync();
        }
    }
}