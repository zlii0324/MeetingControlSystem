import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import App from "./App.jsx";
import "./styles.css";

dayjs.locale("zh-cn");

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#0f8b8d",
          colorSuccess: "#2f855a",
          colorWarning: "#b7791f",
          colorError: "#c2413a",
          colorInfo: "#2563eb",
          borderRadius: 8,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: {
            controlHeight: 38,
            borderRadius: 8,
          },
          Modal: {
            borderRadiusLG: 8,
          },
          Drawer: {
            borderRadiusLG: 8,
          },
          Calendar: {
            borderRadiusLG: 8,
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);

