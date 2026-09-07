/**
 * PC Remote panel. hass.callApi receives a path relative to /api.
 */
class PcRemotePanel extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._mounted = false;
    this._profiles = [];
    this._pairingTimer = undefined;
    this._pairingInitialIds = new Set();
    this._pairingTargetId = undefined;
    this._pairingTargetMarker = undefined;
    this._pairingExpiresAt = 0;
    this._onClick = this._onClick.bind(this);
    this._onImportChange = this._onImportChange.bind(this);
  }

  set hass(hass) {
    this._hass = hass;
    if (this.isConnected) {
      this._mount();
      if (!this._profiles.length) {
        void this._loadProfiles();
      }
    }
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    const wasMounted = this._mounted;
    this._mount();
    if (wasMounted && this._hass) {
      void this._loadProfiles();
    }
  }

  disconnectedCallback() {
    this._stopPairingPoll();
  }

  _mount() {
    if (this._mounted) {
      return;
    }

    this._mounted = true;
    this._root.innerHTML =
      '<style>' +
      ':host{display:block;color:var(--primary-text-color);font-family:var(--primary-font-family,Roboto,Noto,sans-serif)}' +
      '*,*:before,*:after{box-sizing:border-box}' +
      'main{max-width:1180px;margin:0 auto;padding:24px}' +
      'h1,h2,h3,p{margin-top:0}h1{margin-bottom:8px;font-size:28px;font-weight:500}h2{margin:0;font-size:20px;font-weight:500}h3{margin-bottom:10px;font-size:16px;font-weight:500}' +
      '.muted{color:var(--secondary-text-color)}.intro{max-width:820px;margin-bottom:20px;line-height:1.55}' +
      '.toolbar,.button-row,.timer-row,.profile-header,.fields-grid{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.toolbar{margin-bottom:16px}.toolbar .spacer{flex:1 1 8px}' +
      'button{min-height:38px;border:0;border-radius:8px;padding:0 14px;color:var(--primary-text-color);background:var(--secondary-background-color);font:inherit;cursor:pointer}button:hover:not(:disabled){background:var(--divider-color)}button:disabled{cursor:not-allowed;opacity:.55}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}button.primary{color:var(--text-primary-color,#fff);background:var(--primary-color)}button.danger{color:var(--text-primary-color,#fff);background:var(--error-color)}button.warning{color:var(--text-primary-color,#fff);background:var(--warning-color,#f4a000)}' +
      '.notice{display:flex;align-items:flex-start;gap:10px;margin:0 0 16px;padding:12px 14px;border-radius:8px;border-left:4px solid var(--primary-color);background:var(--secondary-background-color);line-height:1.45}.notice[data-level="success"]{border-left-color:var(--success-color,#43a047)}.notice[data-level="error"]{border-left-color:var(--error-color)}.notice[data-level="warning"]{border-left-color:var(--warning-color,#f4a000)}.notice[hidden],.yaml-panel[hidden]{display:none}' +
      '.windows-note{margin-bottom:16px;padding:12px 14px;border-radius:8px;background:var(--secondary-background-color);line-height:1.45}.profiles{display:grid;gap:16px}.profile-card,.empty-state,.yaml-panel{border-radius:12px;background:var(--card-background-color,var(--primary-background-color));box-shadow:var(--ha-card-box-shadow,0 2px 4px rgba(0,0,0,.18))}.profile-card{overflow:hidden}.profile-content{padding:18px}.profile-header{justify-content:space-between;gap:16px;margin-bottom:14px}.badges{display:flex;flex-wrap:wrap;gap:8px}.badge{border-radius:999px;padding:4px 9px;background:var(--secondary-background-color);color:var(--secondary-text-color);font-size:12px;white-space:nowrap}.badge.primary{color:var(--text-primary-color,#fff);background:var(--primary-color)}.badge.ok{color:var(--text-primary-color,#fff);background:var(--success-color,#43a047)}.badge.error{color:var(--text-primary-color,#fff);background:var(--error-color)}.profile-status{margin:0 0 14px;color:var(--secondary-text-color);font-size:14px;line-height:1.5}' +
      'details{margin:16px 0;border-top:1px solid var(--divider-color);border-bottom:1px solid var(--divider-color)}summary{padding:14px 0;cursor:pointer;font-weight:500}.fields-grid{align-items:stretch;padding:0 0 16px}label.field{display:grid;flex:1 1 220px;gap:6px;color:var(--secondary-text-color);font-size:13px}label.field.checkbox-field{display:flex;align-items:center;gap:9px;padding-top:23px}input,select{width:100%;min-height:38px;padding:8px 10px;border:1px solid var(--divider-color);border-radius:6px;color:var(--primary-text-color);background:var(--primary-background-color);font:inherit}input[type="checkbox"]{width:auto;min-height:auto;accent-color:var(--primary-color)}input[readonly]{opacity:.75}.command-section{display:grid;gap:12px;margin-top:14px}.command-section+.command-section{padding-top:14px;border-top:1px solid var(--divider-color)}.timer-row select{width:auto;min-width:106px}.empty-state{padding:30px;text-align:center}.empty-state h2{margin-bottom:10px}.yaml-panel{margin-top:16px;padding:18px}.yaml-panel pre{overflow:auto;max-height:320px;margin:12px 0;padding:12px;border-radius:8px;background:var(--code-editor-background-color,var(--secondary-background-color));color:var(--primary-text-color);white-space:pre-wrap}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;clip-path:inset(50%)}' +
      '@media(max-width:600px){main{padding:16px}.toolbar .spacer{display:none}.toolbar>button{flex:1 1 160px}.profile-header{align-items:flex-start;flex-direction:column}button{flex:1 1 auto}}' +
      '</style>' +
      '<main>' +
      '<h1>PC Remote</h1>' +
      '<p class="intro muted">Сначала интеграция добавляется в «Настройки → Устройства и службы». Сам компьютер привязывается здесь, в этой панели.</p>' +
      '<div class="toolbar">' +
      '<button type="button" data-action="refresh">Обновить</button>' +
      '<button id="pair-button" class="primary" type="button" data-action="pair">Связать с этим ПК</button>' +
      '<button type="button" data-action="show-yaml">Фразы для Алисы</button>' +
      '<span class="spacer"></span>' +
      '<button type="button" data-action="export">Экспорт</button>' +
      '<button type="button" data-action="export-secrets">Экспорт с ключами</button>' +
      '<button type="button" data-action="pick-import">Импорт</button>' +
      '<input id="import-file" class="visually-hidden" type="file" accept="application/json,.json">' +
      '</div>' +
      '<div id="windows-note" class="windows-note" hidden></div>' +
      '<div id="notice" class="notice" role="status" aria-live="polite" hidden></div>' +
      '<section id="profiles" class="profiles" aria-live="polite"></section>' +
      '<section id="yaml-panel" class="yaml-panel" hidden>' +
      '<div class="profile-header"><h2>Фразы для Алисы</h2><button type="button" data-action="hide-yaml">Закрыть</button></div>' +
      '<p class="muted">Добавьте этот фрагмент в существующие сценарии Yandex.Station Intents. Выключение и перезагрузка должны оставаться с подтверждением в сценарии Home Assistant.</p>' +
      '<pre id="yaml-output"></pre><div class="button-row"><button type="button" data-action="copy-yaml">Копировать</button></div>' +
      '</section>' +
      '</main>';

    this._root.addEventListener("click", this._onClick);
    this._root.querySelector("#import-file").addEventListener("change", this._onImportChange);

    if (!this._isWindows()) {
      const pairButton = this._root.querySelector("#pair-button");
      const note = this._root.querySelector("#windows-note");
      pairButton.disabled = true;
      note.hidden = false;
      note.textContent = "Привязку можно начать только в браузере на Windows-компьютере с установленным PC Remote.NET. Откройте эту панель на нужном компьютере.";
    }

    if (this._hass) {
      void this._loadProfiles();
    }
  }

  _isWindows() {
    const platform = navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "";
    return /windows/i.test(platform);
  }

  async _api(method, path, body) {
    if (!this._hass) {
      throw new Error("Home Assistant ещё не подключён к панели.");
    }

    const relativePath = String(path).replace(/^\/?(?:api\/)?/, "");
    try {
      return await this._hass.callApi(method, relativePath, body);
    } catch (error) {
      throw new Error(this._apiErrorMessage(error));
    }
  }

  _apiErrorMessage(error) {
    if (!error) {
      return "Неизвестная ошибка.";
    }
    if (error.body && typeof error.body === "object") {
      if (error.body.error) {
        return String(error.body.error);
      }
      if (error.body.message) {
        return String(error.body.message);
      }
    }
    if (typeof error.body === "string" && error.body.trim()) {
      return error.body;
    }
    if (error.message) {
      return String(error.message);
    }
    if (error.error) {
      return String(error.error);
    }
    return "Не удалось выполнить запрос к Home Assistant.";
  }

  async _onClick(event) {
    const source = event.target;
    const button = source instanceof Element ? source.closest("button[data-action]") : undefined;
    if (!button || button.disabled) {
      return;
    }

    try {
      await this._withButtonDisabled(button, async () => {
        switch (button.dataset.action) {
          case "refresh":
            await this._loadProfiles(true);
            break;
          case "pair":
            await this._startPairing();
            break;
          case "repair-profile":
            await this._repairProfile(button);
            break;
          case "show-yaml":
            await this._showYaml();
            break;
          case "hide-yaml":
            this._root.querySelector("#yaml-panel").hidden = true;
            break;
          case "copy-yaml":
            await this._copyYaml();
            break;
          case "export":
            await this._exportProfiles(false);
            break;
          case "export-secrets":
            await this._exportProfiles(true);
            break;
          case "pick-import":
            this._root.querySelector("#import-file").click();
            break;
          case "save-profile":
            await this._saveProfile(button);
            break;
          case "set-default":
            await this._setDefault(button);
            break;
          case "delete-profile":
            await this._deleteProfile(button);
            break;
          case "wake":
            await this._wake(button);
            break;
          case "command":
            await this._executeCommand(button, button.dataset.command);
            break;
          case "timer-shutdown":
            await this._executeTimer(button, "SHUTDOWN");
            break;
          case "timer-reboot":
            await this._executeTimer(button, "REBOOT");
            break;
          default:
            throw new Error("Неизвестное действие панели.");
        }
      });
    } catch (error) {
      this._setNotice(error.message || String(error), "error");
    }
  }

  async _withButtonDisabled(button, callback) {
    button.disabled = true;
    try {
      await callback();
    } finally {
      button.disabled = false;
    }
  }

  _setNotice(message, level) {
    const notice = this._root.querySelector("#notice");
    notice.textContent = message;
    notice.dataset.level = level || "info";
    notice.hidden = false;
  }

  _clearNotice() {
    const notice = this._root.querySelector("#notice");
    notice.hidden = true;
    notice.textContent = "";
    delete notice.dataset.level;
  }

  async _loadProfiles(announce) {
    try {
      const response = await this._api("GET", "pc_remote/profiles");
      this._profiles = this._extractProfiles(response);
      this._renderProfiles();
      if (announce) {
        this._setNotice("Список компьютеров обновлён.", "success");
      }
    } catch (error) {
      this._setNotice(error.message || String(error), "error");
    }
  }

  _renderProfiles() {
    const container = this._root.querySelector("#profiles");
    container.replaceChildren();

    if (!this._profiles.length) {
      const empty = document.createElement("section");
      empty.className = "empty-state";
      const title = document.createElement("h2");
      title.textContent = "Компьютеры пока не привязаны";
      const text = document.createElement("p");
      text.className = "muted";
      text.textContent = "Откройте эту страницу в браузере на нужном Windows-компьютере и нажмите «Связать с этим ПК».";
      empty.append(title, text);
      container.append(empty);
      return;
    }

    for (const profile of this._profiles) {
      container.append(this._createProfileCard(profile));
    }
  }

  _extractProfiles(response) {
    if (Array.isArray(response)) {
      return response;
    }
    if (response && Array.isArray(response.profiles)) {
      return response.profiles;
    }
    throw new Error("Home Assistant вернул некорректный список компьютеров.");
  }

  _createProfileCard(profile) {
    const card = document.createElement("article");
    card.className = "profile-card";
    card.dataset.profileId = String(profile.id);

    const content = document.createElement("div");
    content.className = "profile-content";
    card.append(content);

    const header = document.createElement("div");
    header.className = "profile-header";
    const titleWrap = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = profile.display_name || "Компьютер";
    titleWrap.append(title);

    const badges = document.createElement("div");
    badges.className = "badges";
    if (profile.is_default) {
      badges.append(this._badge("Основной", "primary"));
    }
    if (profile.last_error) {
      badges.append(this._badge("Ошибка связи", "error"));
    } else if (profile.available) {
      badges.append(this._badge("Связь подтверждена", "ok"));
    } else {
      badges.append(this._badge("Связь не проверена", ""));
    }
    header.append(titleWrap, badges);
    content.append(header);

    const status = document.createElement("p");
    status.className = "profile-status";
    const lastAction = profile.last_action ? "Последнее действие: " + profile.last_action + "." : "Действия ещё не выполнялись.";
    const lastError = profile.last_error ? " Последняя ошибка: " + profile.last_error : "";
    status.textContent = lastAction + lastError;
    content.append(status);

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Имя и сетевые параметры";
    const fields = document.createElement("div");
    fields.className = "fields-grid";
    fields.append(
      this._textField("Название", "display_name", profile.display_name || "Компьютер", "", false),
      this._textField("Локальный IP-адрес", "local_ip", profile.local_ip || "", "например, 192.168.1.20", false),
      this._textField("Внешний IP-адрес", "external_ip", profile.external_ip || "", "", false),
      this._numberField("Порт PC Remote", "port", profile.port || 5055),
      this._textField("MAC-адрес", "mac", profile.mac || "", "AA:BB:CC:DD:EE:FF", false),
      this._textField("Broadcast-адрес", "broadcast_address", profile.broadcast_address || "255.255.255.255", "", false),
      this._numberField("Порт Wake-on-LAN", "wol_port", profile.wol_port || 9),
      this._checkboxField("Использовать Wake-on-LAN", "wol_enabled", Boolean(profile.wol_enabled))
    );
    if (profile.tls_fingerprint) {
      fields.append(this._textField("TLS fingerprint", "tls_fingerprint", profile.tls_fingerprint, "", true));
    }
    details.append(summary, fields);
    content.append(details);

    content.append(
      this._buttonRow(
        this._button("Сохранить параметры", "save-profile", "", "primary", false),
        this._button(profile.is_default ? "Основной компьютер" : "Сделать основным", "set-default", "", "", Boolean(profile.is_default)),
        this._button("Привязать заново", "repair-profile", "", "", !this._isWindows())
      )
    );

    const control = this._section("Управление");
    control.append(
      this._buttonRow(
        this._button("Проверить связь", "command", "STATUS", "", false),
        this._button("Включить (WOL)", "wake", "", "", !profile.wol_enabled || !profile.mac),
        this._button("Заблокировать", "command", "LOCK", "", false),
        this._button("Выключить экран", "command", "SCREEN_OFF", "", false)
      )
    );
    content.append(control);

    const power = this._section("Выключение и перезагрузка");
    const powerNote = document.createElement("p");
    powerNote.className = "muted";
    powerNote.textContent = "Немедленные действия требуют подтверждения. Для отложенных действий доступны фиксированные интервалы.";
    power.append(
      powerNote,
      this._buttonRow(
        this._button("Выключить сейчас", "command", "SHUTDOWN", "danger", false),
        this._button("Перезагрузить сейчас", "command", "REBOOT", "danger", false),
        this._button("Отменить таймер", "command", "CANCEL", "warning", false)
      )
    );

    const timerRow = document.createElement("div");
    timerRow.className = "timer-row";
    const timerSelect = document.createElement("select");
    timerSelect.dataset.field = "timer_minutes";
    timerSelect.setAttribute("aria-label", "Интервал таймера");
    for (const minutes of [15, 30, 60, 90, 120]) {
      const option = document.createElement("option");
      option.value = String(minutes);
      option.textContent = String(minutes) + " мин.";
      timerSelect.append(option);
    }
    timerRow.append(
      timerSelect,
      this._button("Выключить по таймеру", "timer-shutdown", "", "", false),
      this._button("Перезагрузить по таймеру", "timer-reboot", "", "", false)
    );
    power.append(timerRow);
    content.append(power);

    const deleteSection = this._section("Удаление");
    const deleteNote = document.createElement("p");
    deleteNote.className = "muted";
    deleteNote.textContent = "Удаление стирает этот профиль из Home Assistant. Повторная привязка потребует подтверждения на Windows-компьютере.";
    deleteSection.append(deleteNote, this._button("Удалить компьютер", "delete-profile", "", "danger", false));
    content.append(deleteSection);

    return card;
  }

  _section(titleText) {
    const section = document.createElement("section");
    section.className = "command-section";
    const title = document.createElement("h3");
    title.textContent = titleText;
    section.append(title);
    return section;
  }

  _badge(text, style) {
    const element = document.createElement("span");
    element.className = "badge" + (style ? " " + style : "");
    element.textContent = text;
    return element;
  }

  _textField(labelText, field, value, placeholder, readOnly) {
    const label = document.createElement("label");
    label.className = "field";
    label.append(document.createTextNode(labelText));
    const input = document.createElement("input");
    input.type = "text";
    input.dataset.field = field;
    input.value = String(value || "");
    input.placeholder = placeholder || "";
    input.readOnly = Boolean(readOnly);
    label.append(input);
    return label;
  }

  _numberField(labelText, field, value) {
    const label = document.createElement("label");
    label.className = "field";
    label.append(document.createTextNode(labelText));
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = "65535";
    input.step = "1";
    input.dataset.field = field;
    input.value = String(value || "");
    label.append(input);
    return label;
  }

  _checkboxField(labelText, field, checked) {
    const label = document.createElement("label");
    label.className = "field checkbox-field";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.field = field;
    input.checked = checked;
    const text = document.createElement("span");
    text.textContent = labelText;
    label.append(input, text);
    return label;
  }

  _button(text, action, command, className, disabled) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.dataset.action = action;
    if (command) {
      button.dataset.command = command;
    }
    if (className) {
      button.classList.add(className);
    }
    button.disabled = Boolean(disabled);
    return button;
  }

  _buttonRow() {
    const row = document.createElement("div");
    row.className = "button-row";
    row.append(...arguments);
    return row;
  }

  _profileForButton(button) {
    const card = button.closest(".profile-card");
    if (!card) {
      throw new Error("Не удалось определить компьютер.");
    }
    const id = card.dataset.profileId;
    const profile = this._profiles.find((item) => String(item.id) === String(id));
    if (!profile) {
      throw new Error("Профиль компьютера больше не найден. Обновите страницу.");
    }
    return { card, id, profile };
  }

  _readProfileChanges(card) {
    const read = (field) => {
      const input = card.querySelector('[data-field="' + field + '"]');
      return input ? input.value.trim() : "";
    };
    const readPort = (field, title) => {
      const value = Number.parseInt(read(field), 10);
      if (!Number.isInteger(value) || value < 1 || value > 65535) {
        throw new Error(title + ": введите число от 1 до 65535.");
      }
      return value;
    };

    const displayName = read("display_name");
    const localIp = read("local_ip");
    const externalIp = read("external_ip");
    if (!displayName) {
      throw new Error("Укажите название компьютера.");
    }
    if (!localIp && !externalIp) {
      throw new Error("Укажите хотя бы локальный или внешний IP-адрес.");
    }

    const wolInput = card.querySelector('[data-field="wol_enabled"]');
    return {
      display_name: displayName,
      local_ip: localIp,
      external_ip: externalIp,
      port: readPort("port", "Порт PC Remote"),
      mac: read("mac"),
      broadcast_address: read("broadcast_address"),
      wol_port: readPort("wol_port", "Порт Wake-on-LAN"),
      wol_enabled: Boolean(wolInput?.checked),
    };
  }

  async _saveProfile(button) {
    const target = this._profileForButton(button);
    const changes = this._readProfileChanges(target.card);
    await this._api("POST", "pc_remote/profile/" + encodeURIComponent(target.id), changes);
    await this._loadProfiles();
    this._setNotice("Параметры компьютера сохранены.", "success");
  }

  async _setDefault(button) {
    const target = this._profileForButton(button);
    if (target.profile.is_default) {
      return;
    }
    await this._api("POST", "pc_remote/default/" + encodeURIComponent(target.id), {});
    await this._loadProfiles();
    this._setNotice("Основным выбран компьютер «" + (target.profile.display_name || "Компьютер") + "».", "success");
  }

  async _deleteProfile(button) {
    const target = this._profileForButton(button);
    const name = target.profile.display_name || "Компьютер";
    if (!window.confirm("Удалить профиль «" + name + "» из Home Assistant?")) {
      return;
    }
    await this._api("DELETE", "pc_remote/profile/" + encodeURIComponent(target.id));
    await this._loadProfiles();
    this._setNotice("Профиль «" + name + "» удалён.", "success");
  }

  async _repairProfile(button) {
    if (!this._isWindows()) {
      this._setNotice("Откройте Home Assistant в браузере на Windows-компьютере с установленным PC Remote.NET.", "warning");
      return;
    }

    const target = this._profileForButton(button);
    const name = target.profile.display_name || "Компьютер";
    if (!window.confirm("Привязать «" + name + "» заново? Профиль останется в списке, а ключи связи будут заменены.")) {
      return;
    }
    await this._startPairing(target.id);
  }

  async _wake(button) {
    const target = this._profileForButton(button);
    if (!target.profile.mac) {
      throw new Error("Для Wake-on-LAN сначала укажите MAC-адрес и сохраните параметры.");
    }
    await this._api("POST", "pc_remote/wake", { pc_id: target.id });
    await this._loadProfiles();
    this._setNotice("Отправлен Wake-on-LAN для «" + (target.profile.display_name || "Компьютер") + "».", "success");
  }

  async _executeCommand(button, command) {
    const target = this._profileForButton(button);
    if (!command) {
      throw new Error("Команда не выбрана.");
    }
    if (command === "SHUTDOWN" || command === "REBOOT") {
      const action = command === "SHUTDOWN" ? "Выключить" : "Перезагрузить";
      if (!window.confirm(action + " «" + (target.profile.display_name || "Компьютер") + "» сейчас?")) {
        return;
      }
    }
    await this._api("POST", "pc_remote/execute", { pc_id: target.id, command });
    await this._loadProfiles();
    this._setNotice("Команда " + command + " отправлена компьютеру «" + (target.profile.display_name || "Компьютер") + "».", "success");
  }

  async _executeTimer(button, action) {
    const target = this._profileForButton(button);
    const timerSelect = target.card.querySelector('[data-field="timer_minutes"]');
    const minutes = Number.parseInt(timerSelect?.value, 10);
    if (![15, 30, 60, 90, 120].includes(minutes)) {
      throw new Error("Выберите допустимый интервал таймера.");
    }

    const title = action === "SHUTDOWN" ? "выключение" : "перезагрузка";
    if (!window.confirm("Запланировать " + title + " «" + (target.profile.display_name || "Компьютер") + "» через " + minutes + " мин.?")) {
      return;
    }

    await this._api("POST", "pc_remote/execute", {
      pc_id: target.id,
      command: action + ":" + minutes,
    });
    await this._loadProfiles();
    this._setNotice(title.charAt(0).toUpperCase() + title.slice(1) + " запланирована через " + minutes + " мин.", "success");
  }

  async _startPairing(profileId) {
    if (!this._isWindows()) {
      this._setNotice("Откройте Home Assistant в браузере на Windows-компьютере с установленным PC Remote.NET.", "warning");
      return;
    }

    const targetId = profileId ? String(profileId) : undefined;
    const targetProfile = targetId
      ? this._profiles.find((profile) => String(profile.id) === targetId)
      : undefined;
    if (targetId && !targetProfile) {
      throw new Error("Профиль компьютера больше не найден. Обновите страницу.");
    }

    this._clearNotice();
    const response = await this._api(
      "POST",
      "pc_remote/challenge",
      targetId ? { pc_id: targetId } : {}
    );
    if (!response || !response.pairing_uri) {
      throw new Error("Home Assistant не вернул ссылку для привязки.");
    }

    this._pairingInitialIds = new Set(this._profiles.map((profile) => String(profile.id)));
    this._pairingTargetId = targetId;
    this._pairingTargetMarker = targetProfile ? this._profilePairingMarker(targetProfile) : undefined;
    this._pairingExpiresAt = Date.now() + Number(response.expires_in || 120) * 1000;
    this._setNotice(
      targetId
        ? "Подтвердите повторную привязку в PC Remote.NET. Этот профиль будет обновлён, новый компьютер создан не будет."
        : "Подтвердите открытие PC Remote.NET, затем подтвердите разрешение в его окне. Эта страница сама обновит список после успешной привязки.",
      "info"
    );
    this._beginPairingPoll();
    window.location.assign(response.pairing_uri);
  }

  _profilePairingMarker(profile) {
    return JSON.stringify({
      device_id: profile.device_id || "",
      tls_fingerprint: profile.tls_fingerprint || "",
      local_ip: profile.local_ip || "",
      external_ip: profile.external_ip || "",
      port: profile.port || "",
      mac: profile.mac || "",
      broadcast_address: profile.broadcast_address || "",
      wol_port: profile.wol_port || "",
      wol_enabled: Boolean(profile.wol_enabled),
    });
  }

  _beginPairingPoll() {
    this._stopPairingPoll();

    const poll = async () => {
      if (Date.now() >= this._pairingExpiresAt) {
        this._stopPairingPoll();
        this._setNotice("Время привязки истекло. Нажмите «Связать с этим ПК», чтобы создать новую ссылку.", "warning");
        return;
      }

      try {
        const response = await this._api("GET", "pc_remote/profiles");
        const profiles = this._extractProfiles(response);

        const hasNewProfile = profiles.some((profile) => !this._pairingInitialIds.has(String(profile.id)));
        const repairedProfile = this._pairingTargetId
          ? profiles.find((profile) => String(profile.id) === this._pairingTargetId)
          : undefined;
        const hasRepairedProfile = Boolean(
          repairedProfile && this._profilePairingMarker(repairedProfile) !== this._pairingTargetMarker
        );
        this._profiles = profiles;
        this._renderProfiles();

        if (hasNewProfile || hasRepairedProfile) {
          this._stopPairingPoll();
          this._setNotice(
            hasRepairedProfile
              ? "Компьютер успешно привязан заново. Проверьте название и сетевые параметры ниже."
              : "Компьютер успешно привязан. При необходимости измените его название и сетевые параметры ниже.",
            "success"
          );
        }
      } catch {
        // A temporary API error must not cancel a still-valid one-time challenge.
      }
    };

    this._pairingTimer = window.setInterval(() => {
      void poll();
    }, 2500);
    void poll();
  }

  _stopPairingPoll() {
    if (this._pairingTimer) {
      window.clearInterval(this._pairingTimer);
      this._pairingTimer = undefined;
    }
  }

  async _showYaml() {
    const response = await this._api("GET", "pc_remote/alice_yaml");
    if (!response || typeof response.yaml !== "string") {
      throw new Error("Не удалось получить YAML-фрагмент.");
    }
    this._root.querySelector("#yaml-output").textContent = response.yaml;
    this._root.querySelector("#yaml-panel").hidden = false;
  }

  async _copyYaml() {
    const yaml = this._root.querySelector("#yaml-output").textContent || "";
    if (!yaml) {
      throw new Error("Сначала сформируйте фразы для Алисы.");
    }

    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(yaml);
    } else {
      const helper = document.createElement("textarea");
      helper.value = yaml;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      this._root.append(helper);
      helper.select();
      const copied = document.execCommand("copy");
      helper.remove();
      if (!copied) {
        throw new Error("Не удалось скопировать текст. Выделите его вручную.");
      }
    }
    this._setNotice("YAML-фрагмент скопирован в буфер обмена.", "success");
  }

  async _exportProfiles(includeSecrets) {
    if (includeSecrets) {
      const accepted = window.confirm("Этот файл будет содержать ключи, с помощью которых Home Assistant управляет компьютерами. Сохраните его только в защищённом месте и никогда не публикуйте. Продолжить?");
      if (!accepted) {
        return;
      }
    }

    const response = await this._api("POST", "pc_remote/export", {
      include_secrets: includeSecrets,
      confirm: includeSecrets,
    });
    if (!response || !Array.isArray(response.profiles)) {
      throw new Error("Home Assistant вернул некорректный файл экспорта.");
    }

    const suffix = includeSecrets ? "with-secrets" : "settings";
    this._downloadJson(response, "pc-remote-profiles-" + suffix + ".json");
    this._setNotice(includeSecrets ? "Экспорт с ключами скачан. Не передавайте этот файл другим людям." : "Экспорт настроек скачан. В нём нет ключей привязки.", includeSecrets ? "warning" : "success");
  }

  _downloadJson(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.style.display = "none";
    this._root.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async _onImportChange(event) {
    const input = event.target;
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) {
      return;
    }

    try {
      const data = JSON.parse(await file.text());
      if (!data || !Array.isArray(data.profiles)) {
        throw new Error("Выберите экспорт PC Remote: в JSON должен быть массив profiles.");
      }
      if (!window.confirm("Импорт добавит новые профили и обновит профили с совпадающими идентификаторами. Продолжить?")) {
        return;
      }

      await this._api("POST", "pc_remote/import", data);
      await this._loadProfiles();
      this._setNotice(
        data.includes_secrets === true
          ? "Полный экспорт импортирован. Профили готовы к использованию."
          : "Настройки импортированы. Существующие профили сохранили ключи; новые профили нужно привязать заново.",
        "success"
      );
    } catch (error) {
      this._setNotice(error.message || "Не удалось импортировать файл.", "error");
    }
  }
}

if (!customElements.get("pc-remote-panel")) {
  customElements.define("pc-remote-panel", PcRemotePanel);
}
