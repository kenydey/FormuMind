// Extract every HTTP wrapper of the frontend API layer (TypeScript compiler API).
// Usage: node scripts/audit/contract/extract_ts.cjs [out.json]    (default: scripts/audit/contract/data/wrappers.json)
// One record per HTTP call found inside a wrapper: method, normalised path, query / form names, request-body
// fields and the declared response shape. compare.py joins these records with the backend OpenAPI schema.
const path = require("path");
const fs = require("fs");
const REPO = path.resolve(__dirname, "..", "..", "..");
const ts = require(path.join(REPO, "frontend", "node_modules", "typescript"));

const FE = path.join(REPO, "frontend");
const OUT = process.argv[2] || path.join(__dirname, "data", "wrappers.json");
fs.mkdirSync(path.dirname(OUT), { recursive: true });

const cfgPath = path.join(FE, "tsconfig.json");
const cfg = ts.readConfigFile(cfgPath, ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, FE);
const program = ts.createProgram(parsed.fileNames, parsed.options);
const checker = program.getTypeChecker();

const HTTP_FUNCS = { get: "GET", post: "POST", put: "PUT", patch: "PATCH", del: "DELETE", postAccepted: "POST" };

// ---------------------------------------------------------------- type shapes
function typeName(t) {
  const s = t.aliasSymbol || t.symbol;
  return s ? s.getName() : undefined;
}

function shape(type, depth, seen) {
  seen = seen || new Set();
  if (depth <= 0) return { kind: "depth" };
  // unwrap Promise<T>
  const tn = typeName(type);
  if (tn === "Promise" && type.typeArguments && type.typeArguments.length) {
    return shape(type.typeArguments[0], depth, seen);
  }
  if (type.isUnion && type.isUnion()) {
    const parts = type.types.filter(
      (t) => !(t.flags & (ts.TypeFlags.Null | ts.TypeFlags.Undefined | ts.TypeFlags.Void))
    );
    const nullable = parts.length !== type.types.length;
    // boolean = true|false union
    if (parts.length && parts.every((t) => t.flags & ts.TypeFlags.BooleanLiteral)) {
      return { kind: "boolean", nullable };
    }
    if (parts.length && parts.every((t) => t.flags & ts.TypeFlags.StringLiteral)) {
      return { kind: "enum", values: parts.map((t) => t.value), nullable };
    }
    if (parts.length && parts.every((t) => t.flags & ts.TypeFlags.NumberLiteral)) {
      return { kind: "numenum", values: parts.map((t) => t.value), nullable };
    }
    if (parts.length === 1) {
      const s = shape(parts[0], depth, seen);
      s.nullable = nullable || s.nullable;
      return s;
    }
    return { kind: "union", nullable, of: parts.map((t) => shape(t, depth - 1, seen)) };
  }
  if (type.flags & ts.TypeFlags.StringLiteral) return { kind: "enum", values: [type.value] };
  if (type.flags & ts.TypeFlags.String) return { kind: "string" };
  if (type.flags & ts.TypeFlags.Number) return { kind: "number" };
  if (type.flags & ts.TypeFlags.NumberLiteral) return { kind: "numenum", values: [type.value] };
  if (type.flags & ts.TypeFlags.Boolean || type.flags & ts.TypeFlags.BooleanLiteral) return { kind: "boolean" };
  if (type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) return { kind: "any" };
  if (type.flags & ts.TypeFlags.Null) return { kind: "null" };
  if (type.flags & ts.TypeFlags.Undefined) return { kind: "undefined" };
  if (checker.isArrayType(type)) {
    const el = checker.getTypeArguments(type)[0];
    return { kind: "array", item: shape(el, depth - 1, seen) };
  }
  if (checker.isTupleType && checker.isTupleType(type)) {
    return { kind: "tuple", of: checker.getTypeArguments(type).map((t) => shape(t, depth - 1, seen)) };
  }
  if (type.flags & ts.TypeFlags.Object || type.flags & ts.TypeFlags.Intersection) {
    const key = type.id;
    const props = checker.getPropertiesOfType(type);
    const idx = checker.getIndexInfosOfType(type);
    const out = { kind: "object", name: typeName(type), props: {} };
    if (seen.has(key)) return { kind: "ref", name: typeName(type) };
    seen = new Set(seen);
    seen.add(key);
    for (const p of props) {
      const decl = p.valueDeclaration || (p.declarations && p.declarations[0]);
      const pt = decl ? checker.getTypeOfSymbolAtLocation(p, decl) : checker.getTypeOfSymbol(p);
      const optional = !!(p.flags & ts.SymbolFlags.Optional);
      const sh = shape(pt, depth - 1, seen);
      out.props[p.getName()] = Object.assign({ optional }, sh);
    }
    if (idx.length) {
      out.index = shape(idx[0].type, depth - 1, seen);
    }
    return out;
  }
  return { kind: "other", text: checker.typeToString(type) };
}

// ---------------------------------------------------------------- URL helpers
function constInit(id) {
  // identifier -> initializer of const declaration
  const sym = checker.getSymbolAtLocation(id);
  if (!sym || !sym.declarations) return undefined;
  for (const d of sym.declarations) {
    if (ts.isVariableDeclaration(d) && d.initializer) return d.initializer;
  }
  return undefined;
}

// Return list of candidate url strings; `${expr}` markers kept as "${<text>}"
function urlCandidates(expr, depth = 0) {
  if (!expr || depth > 4) return [];
  if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr)) return [expr.text];
  if (ts.isTemplateExpression(expr)) {
    let outs = [expr.head.text];
    for (const span of expr.templateSpans) {
      const marker = "${" + span.expression.getText() + "}";
      outs = outs.map((o) => o + marker + span.literal.text);
    }
    return outs;
  }
  if (ts.isParenthesizedExpression(expr)) return urlCandidates(expr.expression, depth + 1);
  if (ts.isAsExpression(expr)) return urlCandidates(expr.expression, depth + 1);
  if (ts.isBinaryExpression(expr) && expr.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const l = urlCandidates(expr.left, depth + 1);
    const r = urlCandidates(expr.right, depth + 1);
    const res = [];
    for (const a of l.length ? l : ["${?}"]) for (const b of r.length ? r : ["${?}"]) res.push(a + b);
    return res;
  }
  if (ts.isConditionalExpression(expr)) {
    return urlCandidates(expr.whenTrue, depth + 1).concat(urlCandidates(expr.whenFalse, depth + 1));
  }
  if (ts.isIdentifier(expr)) {
    const init = constInit(expr);
    if (init) return urlCandidates(init, depth + 1);
    return ["${" + expr.getText() + "}"];
  }
  return ["${" + expr.getText() + "}"];
}

// normalise like scan_routes.normalize
function normalizePath(lit) {
  // lit contains ${...} markers (with possibly nested braces/backticks in the marker text)
  let out = [];
  let i = 0;
  while (i < lit.length) {
    if (lit.startsWith("${", i)) {
      // find matching close brace
      let depth = 0, j = i + 1;
      for (; j < lit.length; j++) {
        if (lit[j] === "{") depth++;
        else if (lit[j] === "}") { depth--; if (depth === 0) break; }
      }
      if (out.length && out[out.length - 1] !== "/") break; // glued -> suffix/query
      out.push("{}");
      i = j + 1;
      continue;
    }
    if (lit[i] === "?" || lit[i] === "#") break;
    out.push(lit[i]);
    i++;
  }
  return out.join("");
}

function literalQueryNames(lit) {
  // names inside literal text "?a=..&b=" (also after ${..} markers)
  const names = new Set();
  let stripped = lit.replace(/\$\{(?:[^{}]|\{[^{}]*\})*\}/g, "§");
  const m = stripped.match(/[?&]([A-Za-z_][A-Za-z0-9_]*)=/g);
  if (m) m.forEach((x) => names.add(x.slice(1, -1)));
  return [...names];
}

// ---------------------------------------------------------------- collect query/form names inside a function
function collectNames(fnNode) {
  const query = new Set();
  const form = new Set();
  function visit(n) {
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)) {
      const m = n.expression.name.text;
      if ((m === "set" || m === "append") && n.arguments.length >= 1 && ts.isStringLiteralLike(n.arguments[0])) {
        const recvType = checker.getTypeAtLocation(n.expression.expression);
        const tn = typeName(recvType);
        if (tn === "URLSearchParams") query.add(n.arguments[0].text);
        else if (tn === "FormData") form.add(n.arguments[0].text);
      }
    }
    if (ts.isNewExpression(n) && n.expression.getText() === "URLSearchParams" && n.arguments && n.arguments[0]) {
      const a = n.arguments[0];
      if (ts.isObjectLiteralExpression(a)) {
        for (const p of a.properties) {
          if (ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) {
            const nm = p.name.getText().replace(/^["']|["']$/g, "");
            query.add(nm);
          }
        }
      }
    }
    ts.forEachChild(n, visit);
  }
  visit(fnNode);
  return { query: [...query], form: [...form] };
}

// ---------------------------------------------------------------- body helpers
function bodyFields(expr) {
  // returns {kind, fields:[{name, from}], typeText, shape}
  if (!expr) return { kind: "none", fields: [] };
  if (ts.isParenthesizedExpression(expr)) return bodyFields(expr.expression);
  if (ts.isCallExpression(expr) && expr.expression.getText() === "JSON.stringify") return bodyFields(expr.arguments[0]);
  if (ts.isIdentifier(expr) && expr.getText() === "fd") return { kind: "formdata", fields: [] };
  if (ts.isConditionalExpression(expr)) {
    const a = bodyFields(expr.whenTrue);
    const b = bodyFields(expr.whenFalse);
    const names = new Map();
    [...a.fields, ...b.fields].forEach((f) => names.set(f.name, f));
    return { kind: "cond", fields: [...names.values()], typeText: "" };
  }
  const t = checker.getTypeAtLocation(expr);
  if (ts.isObjectLiteralExpression(expr)) {
    const fields = [];
    for (const p of expr.properties) {
      if (ts.isPropertyAssignment(p)) {
        fields.push({ name: p.name.getText().replace(/^["']|["']$/g, ""), from: "literal", valueText: p.initializer.getText().slice(0, 80) });
      } else if (ts.isShorthandPropertyAssignment(p)) {
        fields.push({ name: p.name.getText(), from: "shorthand", valueText: p.name.getText() });
      } else if (ts.isSpreadAssignment(p)) {
        const st = checker.getTypeAtLocation(p.expression);
        const props = checker.getPropertiesOfType(st);
        const optMap = {};
        for (const sp of props) {
          fields.push({ name: sp.getName(), from: "spread:" + (typeName(st) || p.expression.getText()), optional: !!(sp.flags & ts.SymbolFlags.Optional) });
        }
      } else if (ts.isMethodDeclaration(p)) {
        fields.push({ name: p.name.getText(), from: "method" });
      }
    }
    return { kind: "object", fields, typeText: checker.typeToString(t), shape: shape(t, 6) };
  }
  // identifier / property access / other: use the type
  const props = checker.getPropertiesOfType(t);
  const sh = shape(t, 6);
  return {
    kind: "typed",
    fields: props.map((p) => ({ name: p.getName(), from: "type:" + checker.typeToString(t).slice(0, 60), optional: !!(p.flags & ts.SymbolFlags.Optional) })),
    typeText: checker.typeToString(t),
    shape: sh,
    exprText: expr.getText().slice(0, 60),
  };
}

// ---------------------------------------------------------------- response helpers
function respShapeFromCall(call, wrapperFn) {
  // 1. generic type arg
  if (call.typeArguments && call.typeArguments.length) {
    const t = checker.getTypeFromTypeNode(call.typeArguments[0]);
    return { typeText: checker.typeToString(t), shape: shape(t, 7) };
  }
  // 2. declared return type of wrapper
  if (wrapperFn && wrapperFn.type) {
    const t = checker.getTypeFromTypeNode(wrapperFn.type);
    return { typeText: checker.typeToString(t), shape: shape(t, 7), from: "return-annotation" };
  }
  // 3. `as Promise<T>` / `as T` on res.json() inside wrapper
  let found;
  function visit(n) {
    if (found) return;
    if (ts.isAsExpression(n) && n.expression.getText().includes("json()")) {
      found = checker.getTypeFromTypeNode(n.type);
      return;
    }
    ts.forEachChild(n, visit);
  }
  if (wrapperFn) visit(wrapperFn);
  if (found) return { typeText: checker.typeToString(found), shape: shape(found, 7), from: "as-cast" };
  return { typeText: "", shape: null };
}

// ---------------------------------------------------------------- walk apiMethods & friends
const records = [];

function processWrapper(name, fnNode, sf, group) {
  const names = collectNames(fnNode);
  let foundHttp = false;
  function visit(n) {
    if (ts.isCallExpression(n)) {
      let http, urlExpr, bodyExpr, initObj;
      const callee = n.expression;
      if (ts.isIdentifier(callee) && HTTP_FUNCS[callee.text]) {
        http = HTTP_FUNCS[callee.text];
        urlExpr = n.arguments[0];
        bodyExpr = n.arguments[1];
      } else if (ts.isIdentifier(callee) && callee.text === "fetch") {
        urlExpr = n.arguments[0];
        initObj = n.arguments[1];
        http = "GET";
        if (initObj && ts.isObjectLiteralExpression(initObj)) {
          for (const p of initObj.properties) {
            if (ts.isPropertyAssignment(p) && p.name.getText() === "method") {
              http = p.initializer.getText().replace(/["']/g, "").toUpperCase();
            }
            if (ts.isPropertyAssignment(p) && p.name.getText() === "body") bodyExpr = p.initializer;
            if (ts.isShorthandPropertyAssignment(p) && p.name.getText() === "body") bodyExpr = p.name;
          }
        }
      }
      if (http && urlExpr) {
        foundHttp = true;
        const cands = urlCandidates(urlExpr);
        const body = bodyFields(bodyExpr);
        const resp = respShapeFromCall(n, fnNode);
        const pos = sf.getLineAndCharacterOfPosition(n.getStart());
        for (const u of cands) {
          records.push({
            group,
            wrapper: name,
            file: path.relative(FE, sf.fileName),
            line: pos.line + 1,
            http,
            url: u,
            path: normalizePath(u),
            qLiteral: literalQueryNames(u),
            qNames: names.query,
            formNames: names.form,
            body,
            resp,
            acceptedOnly: callee.text === "postAccepted",
          });
        }
      }
    }
    ts.forEachChild(n, visit);
  }
  visit(fnNode);

  if (!foundHttp) {
    // URL-builder wrapper: arrow fn returning a "/api/..." string / template
    const bodyNode = fnNode.body;
    let ret = bodyNode;
    if (bodyNode && ts.isBlock(bodyNode)) {
      for (const st of bodyNode.statements) if (ts.isReturnStatement(st) && st.expression) ret = st.expression;
    }
    const cands = ret ? urlCandidates(ret) : [];
    for (const u of cands) {
      if (u.startsWith("/api/") || u.startsWith("${") && false) {
        const pos = sf.getLineAndCharacterOfPosition(fnNode.getStart());
        records.push({
          group, wrapper: name, file: path.relative(FE, sf.fileName), line: pos.line + 1, http: "GET(url)", url: u,
          path: normalizePath(u), qLiteral: literalQueryNames(u), qNames: names.query, formNames: names.form,
          body: { kind: "none", fields: [] }, resp: { typeText: "", shape: null }, urlBuilder: true,
        });
      }
    }
  }
}

function handleObjectLiteral(obj, sf, group) {
  for (const p of obj.properties) {
    if (ts.isPropertyAssignment(p) && (ts.isArrowFunction(p.initializer) || ts.isFunctionExpression(p.initializer))) {
      processWrapper(p.name.getText(), p.initializer, sf, group);
    } else if (ts.isMethodDeclaration(p)) {
      processWrapper(p.name.getText(), p, sf, group);
    }
  }
}

for (const sf of program.getSourceFiles()) {
  if (sf.fileName.includes("node_modules")) continue;
  const rel = path.relative(FE, sf.fileName);
  if (!(rel === "src/api/methods.ts" || rel === "src/api/extras.ts" || rel.startsWith("src/api/domains/"))) continue;
  ts.forEachChild(sf, function walk(n) {
    if (ts.isVariableStatement(n)) {
      for (const d of n.declarationList.declarations) {
        if (d.initializer && ts.isObjectLiteralExpression(d.initializer)) {
          const nm = d.name.getText();
          if (nm === "apiMethods" || nm === "collectionsApi") handleObjectLiteral(d.initializer, sf, nm);
        }
      }
    }
    if (ts.isFunctionDeclaration(n) && n.name && rel.endsWith("extras.ts")) {
      processWrapper(n.name.text, n, sf, "extras");
    }
  });
}

fs.writeFileSync(OUT, JSON.stringify(records, null, 1));
console.log("records:", records.length, "wrappers:", new Set(records.map((r) => r.group + ":" + r.wrapper)).size);
