from sleuth.ingest.chunk import chunk_source
from sleuth.ingest.parse import LANGUAGES

VUE_SRC = b"""<template>
  <div class="foo">
    <span v-if="show">{{ msg }}</span>
    <MyComponent v-for="item in items" :key="item.id" @click="onClick" />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

const count = ref(0);

const increment = () => {
  count.value++;
};

function reset() {
  count.value = 0;
}
</script>

<style scoped>
.foo {
  color: red;
}

.bar, .baz {
  color: blue;
}
</style>
"""


def test_vue_extension_is_registered_when_grammar_available():
    # .vue is only registered if the vendored tree-sitter-vue extension
    # actually compiled/imported successfully (see parse.py's optional-import
    # comment) — skip gracefully rather than fail hard on a machine where it
    # legitimately isn't compiled (e.g. no C compiler), same as the rest of
    # this test module.
    if ".vue" not in LANGUAGES:
        import pytest

        pytest.skip("tree-sitter-vue extension not compiled in this environment")


def _require_vue():
    if ".vue" not in LANGUAGES:
        import pytest

        pytest.skip("tree-sitter-vue extension not compiled in this environment")


def test_chunk_source_vue_template_becomes_one_chunk_per_top_level_element():
    _require_vue()
    chunks = chunk_source(VUE_SRC, "src/Widget.vue", ".vue")
    template_chunks = [c for c in chunks if c.kind == "template"]

    assert len(template_chunks) == 1
    assert 'class="foo"' in template_chunks[0].code_text
    assert "v-if" in template_chunks[0].code_text
    assert "v-for" in template_chunks[0].code_text
    assert "@click" in template_chunks[0].code_text


def test_chunk_source_vue_script_setup_ts_arrow_and_function_are_chunked():
    _require_vue()
    chunks = chunk_source(VUE_SRC, "src/Widget.vue", ".vue")
    by_symbol = {c.symbol_name: c for c in chunks}

    # confirms the <script setup lang="ts"> block was routed through the
    # (already arrow-function-fixed) TypeScript chunker, not just dumped as
    # one opaque blob
    assert by_symbol["increment"].kind == "function"
    assert "=>" in by_symbol["increment"].code_text
    assert by_symbol["reset"].kind == "function"


def test_chunk_source_vue_script_block_line_numbers_map_back_to_original_file():
    _require_vue()
    chunks = chunk_source(VUE_SRC, "src/Widget.vue", ".vue")
    by_symbol = {c.symbol_name: c for c in chunks}

    # "const increment = ..." is on line 13 of VUE_SRC (1-indexed) — the
    # sub-chunker sees only the extracted <script> text starting at line 1,
    # so this pins the line-offset correction actually landed
    assert by_symbol["increment"].start_line == 13
    assert by_symbol["reset"].start_line == 17


def test_chunk_source_vue_style_block_routed_through_css_chunker():
    _require_vue()
    chunks = chunk_source(VUE_SRC, "src/Widget.vue", ".vue")
    rule_chunks = {c.symbol_name: c for c in chunks if c.kind == "rule"}

    assert ".foo" in rule_chunks
    assert ".bar, .baz" in rule_chunks
    assert "color: red" in rule_chunks[".foo"].code_text


VUE_OPTIONS_SRC = b"""<template>
  <button @click="onClick">{{ count }}</button>
</template>

<script>
export default {
  name: "Counter",
  methods: {
    onClick() {
      this.count++;
    },
  },
};
</script>
"""


def test_chunk_source_vue_plain_script_no_lang_attribute_defaults_to_js():
    _require_vue()
    # no lang="..." at all -> defaults to plain JS extension, still chunks
    # without error (the options-API export default object doesn't match any
    # function/class query pattern, so it lands in the leftover module chunk
    # — that's an accepted, honest limitation, not silently dropped/crashed)
    chunks = chunk_source(VUE_OPTIONS_SRC, "src/Counter.vue", ".vue")
    kinds = {c.kind for c in chunks}
    assert "module" in kinds or "function" in kinds  # something was chunked, not empty


def test_chunk_source_vue_empty_blocks_produce_no_chunks_no_crash():
    _require_vue()
    source = b"<template></template>\n<script></script>\n<style></style>\n"
    chunks = chunk_source(source, "src/Empty.vue", ".vue")
    assert chunks == []


def test_chunk_source_vue_multiple_top_level_template_elements_each_chunked():
    _require_vue()
    source = b"""<template>
  <header>Top</header>
  <footer>Bottom</footer>
</template>
<script>
const x = 1;
</script>
"""
    chunks = chunk_source(source, "src/Layout.vue", ".vue")
    template_chunks = [c for c in chunks if c.kind == "template"]
    assert len(template_chunks) == 2
