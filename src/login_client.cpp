#include <iostream>
#include <string>
#include <curl/curl.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

// 回调函数用于接收HTTP响应
size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
    userp->append((char*)contents, size * nmemb);
    return size * nmemb;
}

class ChaynsLoginClient {
public:
    ChaynsLoginClient() {
        curl_global_init(CURL_GLOBAL_DEFAULT);
    }

    ~ChaynsLoginClient() {
        curl_global_cleanup();
    }

    bool login(const std::string& username, const std::string& password, json& response) {
        CURL* curl = curl_easy_init();
        if (!curl) {
            std::cerr << "Failed to initialize CURL" << std::endl;
            return false;
        }

        std::string readBuffer;
        json requestData;
        requestData["username"] = username;
        requestData["password"] = password;
        std::string jsonStr = requestData.dump();

        struct curl_slist* headers = NULL;
        headers = curl_slist_append(headers, "Content-Type: application/json");

        curl_easy_setopt(curl, CURLOPT_URL, "http://127.0.0.1:5000/login");
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, jsonStr.c_str());
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &readBuffer);

        CURLcode res = curl_easy_perform(curl);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);

        if (res != CURLE_OK) {
            std::cerr << "curl_easy_perform() failed: " << curl_easy_strerror(res) << std::endl;
            return false;
        }

        try {
            response = json::parse(readBuffer);
            return true;
        } catch (const json::parse_error& e) {
            std::cerr << "JSON parse error: " << e.what() << std::endl;
            return false;
        }
    }
};

// 使用示例
int main() {
    ChaynsLoginClient client;
    json response;

    if (client.login("test@example.com", "password", response)) {
        if (response.contains("error")) {
            std::cout << "Login failed: " << response["error"] << std::endl;
        } else {
            std::cout << "Login successful!" << std::endl;
            std::cout << "TobitUserID: " << response["TobitUserID"] << std::endl;
            std::cout << "PersonID: " << response["PersonID"] << std::endl;
            std::cout << "Token: " << response["TobitAccessToken"] << std::endl;
        }
    } else {
        std::cout << "Failed to communicate with login service" << std::endl;
    }

    return 0;
} 