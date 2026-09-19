import type { ThemeConfig } from "antd";

const theme: ThemeConfig = {
  token: {
    colorPrimary: "#235cff",
    borderRadius: 6,
    colorBgContainer: "#ffffff",
  },
  components: {
    Button: {
      colorPrimary: "#235cff",
    },
    Menu: {
      itemSelectedBg: "#e8f0fe",
      itemSelectedColor: "#235cff",
    },
  },
};

export default theme;