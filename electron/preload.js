const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("mvGenerator", {
  appName: "Music Video Generator"
});
