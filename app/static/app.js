(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  /* ------------------------------------------------------------ navigation */
  const body = document.body;
  const navToggle = $("[data-nav-toggle]");
  if (navToggle) {
    navToggle.addEventListener("click", () => body.classList.toggle("nav-open"));
  }
  body.addEventListener("click", (event) => {
    if (!body.classList.contains("nav-open")) return;
    if (event.target.closest(".sidebar") || event.target.closest("[data-nav-toggle]")) return;
    body.classList.remove("nav-open");
  });

  /* ---------------------------------------------------------- confirm flow */
  const dialog = $("#confirm-dialog");
  let pendingSubmit = null;
  let pendingSubmitter = null;

  const openConfirm = (message, onConfirm) => {
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(message)) onConfirm();
      return;
    }
    const messageNode = $("#confirm-message");
    if (messageNode) messageNode.textContent = message;
    pendingSubmit = onConfirm;
    dialog.showModal();
  };

  if (dialog) {
    const cancel = $("#confirm-cancel");
    const accept = $("#confirm-accept");
    if (cancel) {
      cancel.addEventListener("click", () => {
        pendingSubmit = null;
        pendingSubmitter = null;
        dialog.close();
      });
    }
    if (accept) {
      accept.addEventListener("click", () => {
        const action = pendingSubmit;
        pendingSubmit = null;
        dialog.close();
        if (action) action();
      });
    }
    dialog.addEventListener("cancel", () => {
      pendingSubmit = null;
      pendingSubmitter = null;
    });
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.confirmed === "true") {
      delete form.dataset.confirmed;
      markSubmitting(form);
      return;
    }

    const submitter = event.submitter || null;
    let message = submitter?.dataset.confirm || form.dataset.confirm || "";
    if (!message && submitter?.dataset.confirmParse !== undefined) {
      const source = form.dataset.dirtyScope
        ? $(form.dataset.dirtyScope)
        : form;
      if (source && formIsDirty(source, { ignoreFile: true })) {
        message = submitter.dataset.confirmParse;
      }
    }
    if (!message && form.dataset.confirmIfDirty !== undefined) {
      const source = form.dataset.dirtyScope
        ? $(form.dataset.dirtyScope)
        : form;
      if (source && formIsDirty(source)) {
        message = form.dataset.confirmIfDirty;
      }
    }
    if (!message) {
      markSubmitting(form);
      return;
    }

    event.preventDefault();
    pendingSubmitter = submitter;
    openConfirm(message, () => {
      form.dataset.confirmed = "true";
      const button = pendingSubmitter;
      pendingSubmitter = null;
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(button);
      } else {
        form.submit();
      }
    });
  });

  const formIsDirty = (scope, options = {}) => {
    return $$("input, select, textarea", scope).some((field) => {
      if (field.type === "hidden" || field.type === "submit" || field.disabled) return false;
      if (field.type === "file") {
        return options.ignoreFile ? false : Boolean(field.files && field.files.length);
      }
      if (field.type === "checkbox" || field.type === "radio") return field.checked;
      return String(field.value || "").trim() !== "";
    });
  };

  const markSubmitting = (form) => {
    $$("button[type=submit], input[type=submit]", form).forEach((button) => {
      window.setTimeout(() => {
        button.disabled = true;
        button.dataset.wasDisabled = "true";
      }, 0);
    });
  };

  /* --------------------------------------------------------------- copying */
  $$("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const selector = button.dataset.copy;
      const target = selector ? $(selector) : null;
      const value = target ? (target.textContent || "").trim() : button.dataset.copyValue || "";
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        const area = document.createElement("textarea");
        area.value = value;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      const original = button.textContent;
      button.textContent = "已复制";
      window.setTimeout(() => {
        button.textContent = original;
      }, 1600);
    });
  });

  /* ------------------------------------------------------------ document lines */
  const linesBody = $("#line-rows");
  const lineTemplate = $("#line-row-template");

  const refreshLineNumbers = () => {
    if (!linesBody) return;
    $$("tr.line-row", linesBody).forEach((row, index) => {
      const number = $(".line-no", row);
      if (number) number.textContent = String(index + 1);
    });
  };

  const applyMaterialMeta = (row) => {
    const select = $("select[name='line_material_id']", row);
    if (!select) return;
    const option = select.selectedOptions[0];
    const quantity = $("input[name='line_quantity']", row);
    const unit = $(".line-unit", row);
    if (unit) unit.textContent = option?.dataset.unit || "";
    if (quantity && option) {
      const integer = option.dataset.quantityType === "INTEGER";
      quantity.step = integer ? "1" : "0.001";
      quantity.inputMode = integer ? "numeric" : "decimal";
      quantity.placeholder = integer ? "整数" : "0.000";
    }
  };

  const bindLineRow = (row) => {
    const material = $("select[name='line_material_id']", row);
    if (material) {
      material.addEventListener("change", () => applyMaterialMeta(row));
      applyMaterialMeta(row);
    }
    const remove = $("[data-remove-line]", row);
    if (remove) {
      remove.addEventListener("click", () => {
        if ($$("tr.line-row", linesBody).length <= 1) return;
        row.remove();
        refreshLineNumbers();
      });
    }
    const applyProject = $("[data-apply-project]", row);
    if (applyProject) {
      applyProject.addEventListener("click", () => {
        const project = $("select[name='line_project_id']", row);
        if (!project || !linesBody) return;
        $$("tr.line-row", linesBody).forEach((other) => {
          const target = $("select[name='line_project_id']", other);
          if (target) target.value = project.value;
        });
      });
    }
  };

  if (linesBody) {
    $$("tr.line-row", linesBody).forEach(bindLineRow);

    const addLine = $("#add-line");
    if (addLine && lineTemplate) {
      addLine.addEventListener("click", () => {
        const fragment = lineTemplate.content.cloneNode(true);
        const row = fragment.querySelector("tr.line-row");
        linesBody.appendChild(fragment);
        bindLineRow(row);
        refreshLineNumbers();
        const firstInput = $("select[name='line_material_id']", row);
        if (firstInput) firstInput.focus();
        if (window.htmx) window.htmx.process(row);
      });
    }
  }

  const toggleLines = $("#toggle-lines");
  if (toggleLines && linesBody) {
    toggleLines.addEventListener("click", () => {
      const collapsed = toggleLines.dataset.collapsed !== "false";
      $$("tr.line-row.is-collapsed", linesBody).forEach((row) => {
        row.style.display = collapsed ? "table-row" : "";
      });
      toggleLines.dataset.collapsed = collapsed ? "false" : "true";
      toggleLines.textContent = collapsed
        ? "收起后 100 行"
        : `显示全部 ${$$("tr.line-row", linesBody).length} 行`;
    });
  }

  const warehouse = $("#warehouse_id");
  if (warehouse) {
    warehouse.addEventListener("change", () => {
      if (!window.htmx) return;
      $$("select[name='line_material_id']").forEach((select) => {
        window.htmx.trigger(select, "change");
      });
    });
  }

  /* -------------------------------------------------------------- file input */
  $$(".dropzone").forEach((zone) => {
    const input = $("input[type=file]", zone);
    const name = $(".file-name", zone);
    if (!input) return;
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (name) name.textContent = file ? file.name : "";
      zone.classList.toggle("has-file", Boolean(file));
    });
    ["dragenter", "dragover"].forEach((eventName) => {
      zone.addEventListener(eventName, () => zone.classList.add("is-dragover"));
    });
    ["dragleave", "drop"].forEach((eventName) => {
      zone.addEventListener(eventName, () => zone.classList.remove("is-dragover"));
    });
  });

  /* ------------------------------------------------------------ auto submit */
  $$("[data-auto-submit]").forEach((field) => {
    field.addEventListener("change", () => {
      const form = field.closest("form");
      if (form) form.requestSubmit();
    });
  });

  /* ---------------------------------------------------------- role hint */
  const roleSelect = $("#role");
  const roleHint = $("#role-hint");
  if (roleSelect && roleHint) {
    const messages = {
      COMPANY_ADMIN: "公司管理员可管理用户、基础资料、审计与备份。",
      OPERATOR: "库存操作员可查询、建单、提交与软删除本人单据，不能管理用户或基础资料。",
    };
    const update = () => {
      roleHint.textContent = messages[roleSelect.value] || "";
    };
    roleSelect.addEventListener("change", update);
    update();
  }
})();
