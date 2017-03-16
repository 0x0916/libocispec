import os, sys, json

class Node:

    def validate_name(self, name):
        if name == "linux":
            return "os_linux"
        if name == "windows":
            return "os_windows"
        if name == "solaris":
            return "os_solaris"
        return name
    def __init__(self, name, typ, children, subtyp=None, subtypobj=None):
        self.name = self.validate_name(name)
        self.typ = typ
        self.children = children
        self.subtyp = subtyp
        self.subtypobj = subtypobj

    def __repr__(self):
        if self.subtyp is not None:
            return "name:(%s) type:(%s -> %s)" % (self.name, self.typ, self.subtyp)
        return "name:(%s) type:(%s)" % (self.name, self.typ)

c_types_mapping = {
    'string' : 'char *',
    'integer' : 'int',
    'boolean' : 'bool',
    'int8' : 'int8_t',
    "int16" : 'int16_t',
    "int32" : "int32_t",
    "int64" : "int64_t",
    'uint8' : 'uint8_t',
    "uint16" : 'uint16_t',
    "uint32" : "uint32_t",
    "uint64" : "uint64_t",
    "UID" : "uid_t",
    "GID" : "gid_t",
    "mapStringString" : "string_cells *",
    "ArrayOfStrings" : "string_cells *",
}

def make_name(name):
    return "oci_container_%s" % name
    
def make_pointer(name, typ):
    if typ != 'object':
        return None
    return "%s *" % make_name(name)

def get_pointer(name, typ):
    ptr = make_pointer(name, typ)
    if ptr:
        return ptr
    if typ == "string":
        return "char *"
    if typ in ["mapStringString", "ArrayOfStrings"]:
        return "%s *" % typ
    return None

def append_C_code(obj, c_file):
    if obj.typ != 'object' and obj.typ != 'array':
        return

    typename = make_name(obj.name)
    if obj.typ == 'object':
        objs = obj.children
        c_file.write("void free_%s(%s *ptr) {\n" % (typename, typename))
    if obj.typ == 'array':
        objs = obj.subtypobj
        if objs is None:
            return
        typename = typename + "_element"
        c_file.write("void free_%s(%s *ptr) {\n" % (typename, typename))

    for i in objs:
        if i.typ == 'array':
            if i.subtypobj is not None:
                free_func = make_name(i.name) + "_element"
                c_file.write("    if (ptr->%s)\n" % i.name)
                c_file.write("        free_%s (ptr->%s);\n\n" % (free_func, i.name))

            c_typ = get_pointer(i.name, i.subtypobj)
            if c_typ == None:
                continue
            if i.subobj is not None:
                c_typ = c_typ + "_element"
            cleanup_code = """    if (ptr->%s) {
        %s *it = ptr->%s;
        while (it) {
            free (*it);
            it++;
        }
    }
""" % (i.name, c_typ, i.name)
            c_file.write(cleanup_code)
        else:
            c_typ = get_pointer(i.name, i.typ)
            if c_typ == None:
                continue
            cleanup_code = """    if (ptr->%s) {
        free (ptr->%s);
    }
""" % (i.name, i.name)
            c_file.write(cleanup_code)
    c_file.write("}\n\n")
    
def append_type_C_header(obj, header):
    if obj.typ == 'array':
        if not obj.subtypobj:
            return
        header.write("typedef struct {\n")
        for i in obj.subtypobj:
            if i.typ == 'array':
                c_typ = make_pointer(i.name, i.subtyp) or c_types_mapping[i.subtyp]
                if i.subtypobj is not None:
                    c_typ = make_name(i.name) + "_element"
                header.write("    %s%s*%s;\n" % (c_typ, " " if '*' not in c_typ else "", i.name))
            else:
                c_typ = make_pointer(i.name, i.typ) or c_types_mapping[i.typ]
                header.write("    %s%s%s;\n" % (c_typ, " " if '*' not in c_typ else "", i.name))
        typename = make_name(obj.name) + "_element"
        header.write("} %s;\n\n" % typename)
        header.write("void free_%s(%s *ptr);\n\n" % (typename, typename))
    elif obj.typ == 'object':
        header.write("typedef struct {\n")
        for i in obj.children:
            if i.typ == 'array':
                if i.subtypobj is not None:
                    c_typ = make_name(i.name) + "_element"
                else:
                    c_typ = make_pointer(i.name, i.subtyp) or c_types_mapping[i.subtyp]
                header.write("    %s%s*%s;\n" % (c_typ, " " if '*' not in c_typ else "", i.name))
            else:
                c_typ = make_pointer(i.name, i.typ) or c_types_mapping[i.typ]
                header.write("    %s%s%s;\n" % (c_typ, " " if '*' not in c_typ else "", i.name))

        typename = make_name(obj.name)
        header.write("} %s;\n" % typename)
        header.write("void free_%s(%s *ptr);\n\n" % (typename, typename))

def get_ref(src, ref):
    f, r = ref.split("#/")
    if f == "":
        cur = src
    else:
        with open(f) as i:
            cur = src = json.loads(i.read())

    for j in r.split('/'):
        basic_types = [
            "int8", "int16", "int32", "int64",
            "uint8", "uint16", "uint32", "uint64", "UID", "GID",
            "mapStringString", "ArrayOfStrings"
        ]
        if j in basic_types:
            return src, {"type" : j}
        cur = cur[j]

    if 'type' not in cur and '$ref' in cur:
        return get_ref(src, cur['$ref'])

    return src, cur

def resolve_type(name, src, cur):
    if '$ref' in cur:
        src, cur = get_ref(src, cur['$ref'])

    if 'patternProperties' in cur:
        # if a patternProperties, take the first value
        typ = cur['patternProperties'].values()[0]["type"]
    elif "type" in cur:
        typ = cur["type"]
    else:
        typ = "object"

    children = None
    subtyp = None
    subtypobj = None
    if typ == 'array':
        if 'allOf' in cur["items"]:
            children = scan_list(name, src, cur["items"]['allOf'])
            subtyp = children[0].typ
            subtypobj = children
        elif 'anyOf' in cur["items"]:
            children = scan_list(name, src, cur["items"]['anyOf'])
            subtyp = children[0].typ
            subtypobj = children
        elif '$ref' in cur["items"]:
            item_type, src = resolve_type(name, src, cur["items"])
            return Node(name, typ, None, subtyp=item_type.typ, subtypobj=item_type.children), src
        elif 'type' in cur["items"]:
            item_type, src = resolve_type(name, src, cur["items"])
            return Node(name, typ, None, subtyp=item_type.typ, subtypobj=item_type.children), src
    elif typ == 'object':
        if 'allOf' in cur:
            children = scan_list(name, src, cur['allOf'])
        elif 'anyOf' in cur:
            children = scan_list(name, src, cur['anyOf'])
        else:
            children = scan_properties(name, src, cur) if 'properties' in cur else None
    else:
        children = None

    return Node(name, typ, children, subtyp=subtyp, subtypobj=subtypobj), src

def scan_list(name, schema, objs):
    obj = []
    for i in objs:
        generated_name = i['$ref'].split("/")[-1] if '$ref' in i else name
        node, _ = resolve_type(generated_name, schema, i)
        if node:
            obj.append(node)
    return obj

def scan_dict(name, schema, objs):
    obj = []
    if name != "":
        name = name + '_'
    for i in objs:
        node, _ = resolve_type(name + i, schema, objs[i])
        if node:
            obj.append(node)
    return obj

def scan_properties(name, schema, props):
    return scan_dict(name, schema, props['properties'])

def scan_main(schema):
    return Node("container", "object", scan_properties("", schema, schema))

def flatten(tree, structs, visited={}):
    if tree.children is not None:
        for i in tree.children:
            flatten(i, structs, visited=visited)
    if tree.subtypobj is not None:
        for i in tree.subtypobj:
            flatten(i, structs, visited=visited)

    id_ = "%s:%s" % (tree.name, tree.typ)
    if id_ not in visited.keys():
        structs.append(tree)
        visited[id_] = tree

    return structs

def generate_C_header(structs, header):
    header.write("// autogenerated file\n")
    header.write("#ifndef SCHEMA_H\n")
    header.write("# define SCHEMA_H\n\n")
    header.write("# include <sys/types.h>\n")
    header.write("# include <stdbool.h>\n")
    header.write("# include <stdint.h>\n\n")

    header.write("typedef struct {\n    char *key;\n    char *value;\n} string_cells;\n\n")

    for i in structs:
        append_type_C_header(i, header_file)
    header.write("#endif\n")

def generate_C_code(structs, header_name, c_file):
    c_file.write("// autogenerated file\n")
    c_file.write('#include <stdlib.h>\n')
    c_file.write('#include "%s"\n\n' % header_name)

    for i in structs:
        append_C_code(i, c_file)

def generate(schema_json, header_name, header_file, c_file):
    tree = scan_main(schema_json)
    # we could do this in scan_main, but let's work on tree that is easier
    # to access.
    structs = flatten(tree, [])
    generate_C_header(structs, header_file)
    generate_C_code(structs, header_name, c_file)

if __name__ == "__main__":
    with open("config-schema.json") as f:
        schema_json = json.loads(f.read())
    header = sys.argv[1]
    c_source = sys.argv[2]
    with open(header, "w") as header_file, open(c_source, "w") as c_file:
        generate(schema_json, header, header_file, c_file)
