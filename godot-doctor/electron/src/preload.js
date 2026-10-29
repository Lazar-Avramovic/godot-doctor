'use strict';

const { contextBridge, ipcRenderer } = require('electron');

const PICK_FOLDER = 'godot-doctor:pick-folder';
const GET_THEME = 'godot-doctor:get-theme';
const SET_THEME = 'godot-doctor:set-theme';

contextBridge.exposeInMainWorld('godotDoctor', {
  initialTheme: ipcRenderer.sendSync(GET_THEME),
  setTheme: theme => ipcRenderer.invoke(SET_THEME, theme),
  pickFolder: hint => ipcRenderer.invoke(
    PICK_FOLDER,
    typeof hint === 'string' ? hint : '',
  ),
});
