# This is derived from the Pyste version of ClassExporter.py.
# See http://www.boost.org/ for more information.

import exporters
from Exporter import Exporter
from declarations import *
from settings import *
from policies import *
from EnumExporter import EnumExporter
from utils import makeid, enumerate
import copy
import exporterutils
import re

#==============================================================================
# ValueTypeExporter
#==============================================================================
class ValueTypeExporter(Exporter):
    'Generates boost.python code to export a class declaration'
 
    def __init__(self, info, parser_tail=None):
        Exporter.__init__(self, info, parser_tail)
        # sections of code
        self.sections = {}
        # template: each item in the list is an item into the class_<...> 
        # section.
        self.sections['template'] = []  
        # constructor: each item in the list is a parameter to the class_ 
        # constructor, like class_<C>(...)
        self.sections['constructor'] = []
        # inside: everything within the class_<> statement        
        self.sections['inside'] = []        
        # scope: items outside the class statement but within its scope.
        # scope* s = new scope(class<>());
        # ...
        # delete s;
        self.sections['scope'] = []
        # declarations: outside the BOOST_PYTHON_MODULE macro
        self.sections['declaration'] = []
        self.sections['declaration-outside'] = []
        self.sections['include'] = []
        # a list of Constructor instances
        self.constructors = []
        self.wrapper_generator = None
        # a list of code units, generated by nested declarations
        self.nested_codeunits = []


    def ScopeName(self):
        return makeid(self.class_.FullName()) + '_scope'


    def Name(self):
        return self.info.name


    def SetDeclarations(self, declarations):
        Exporter.SetDeclarations(self, declarations)
        if self.declarations:
            decl = self.GetDeclaration(self.info.name)
            if isinstance(decl, Typedef):
                self.class_ = self.GetDeclaration(decl.type.name)
                if not self.info.rename:
                    self.info.rename = decl.name
            else:
                self.class_ = decl
            self.class_ = copy.deepcopy(self.class_)
        else:
            self.class_ = None
        
        
    def Export(self, codeunit, exported_names):
        self.InheritMethods(exported_names)
        if not self.info.exclude:
            self.ExportBasics()
            self.ExportConstructors()
            self.ExportVariables()
            self.ExportMethods()
            self.ExportOperators()
            self.ExportNestedValueTypes(exported_names)
            self.ExportNestedEnums(exported_names)
            self.ExportSmartPointer()
            self.ExportOpaquePointerPolicies()
            self.Write(codeunit)
            exported_names[self.Name()] = 1


    def InheritMethods(self, exported_names):
        '''Go up in the class hierarchy looking for classes that were not
        exported yet, and then add their public members to this classes
        members, as if they were members of this class. This allows the user to
        just export one type and automatically get all the members from the
        base classes.
        '''
        valid_members = (Method, ClassVariable, NestedClass, ClassEnumeration)
            # these don't work INVESTIGATE!: (ClassOperator, ConverterOperator)
        fullnames = [x.FullName() for x in self.class_]
        pointers = [x.PointerDeclaration(True) for x in self.class_ if isinstance(x, Method)]
        fullnames = dict([(x, None) for x in fullnames])
        pointers = dict([(x, None) for x in pointers])
        for level in self.class_.hierarchy:
            level_exported = False
            for base in level:
                base = self.GetDeclaration(base.name)
                if base.FullName() not in exported_names:
                    for member in base:
                        if type(member) in valid_members:
                            member_copy = copy.deepcopy(member)   
                            member_copy.class_ = self.class_.FullName()
                            if isinstance(member_copy, Method):
                                pointer = member_copy.PointerDeclaration(True)
                                if pointer not in pointers:
                                    self.class_.AddMember(member)
                                    pointers[pointer] = None
                            elif member_copy.FullName() not in fullnames:
                                self.class_.AddMember(member)        
                else:
                    level_exported = True
            if level_exported:
                break
        def IsValid(member):
            return isinstance(member, valid_members) and member.visibility == Scope.public
        self.public_members = [x for x in self.class_ if IsValid(x)] 


    def Write(self, codeunit):
        indent = self.INDENT
        pyste_ns = namespaces.pyste
        code = ''
        # export the template section
        code += indent + 'public struct %s' % self.class_.FullName()
        # export the constructor section
        constructor_params = ', '.join(self.sections['constructor'])
        code += '(%s)\n' % constructor_params
        # export the inside section
        in_indent = indent*2
        for line in self.sections['inside']:
            code += in_indent + line + '\n' 
        # write the scope section and end it
        if not needs_scope:
            code += indent + ';\n'
        else:
            code += indent + ');\n'
            for line in self.sections['scope']:
                code += indent + line + '\n'
            # write the contents of the nested classes
            for nested_unit in nested_codeunits:
                code += '\n' + nested_unit.Section('module')
            # close the scope
            code += indent + 'delete %s;\n' % scope_name
            
        # write the code to the module section in the codeunit        
        codeunit.Write('module', code + '\n')
        
        # write the declarations to the codeunit        
        declarations = '\n'.join(self.sections['declaration'])
        for nested_unit in nested_codeunits:
            declarations += nested_unit.Section('declaration')
        if declarations:
            codeunit.Write('declaration', declarations + '\n')
        declarations_outside = '\n'.join(self.sections['declaration-outside'])
        if declarations_outside:
            codeunit.Write('declaration-outside', declarations_outside + '\n')

        # write the includes to the codeunit
        includes = '\n'.join(self.sections['include'])
        for nested_unit in nested_codeunits:
            includes += nested_unit.Section('include')
        if includes:
            codeunit.Write('include', includes)


    def Add(self, section, item):
        'Add the item into the corresponding section'
        self.sections[section].append(item)

        
    def ExportBasics(self):
        '''Export the name of the class and its class_ statement.'''
        class_name = self.class_.FullName()
        self.Add('template', class_name)
        name = self.info.rename or self.class_.name
        self.Add('constructor', '"%s"' % name)


    def ExportConstructors(self):
        '''Exports all the public contructors of the class, plus indicates if the 
        class is noncopyable.
        '''
        py_ns = namespaces.python
        indent = self.INDENT
        
        def init_code(cons):
            'return the init<>() code for the given contructor'
            param_list = [p.FullName() for p in cons.parameters]
            min_params_list = param_list[:cons.minArgs]
            max_params_list = param_list[cons.minArgs:]
            min_params = ', '.join(min_params_list)
            max_params = ', '.join(max_params_list)
            init = py_ns + 'init< '
            init += min_params
            if max_params:
                if min_params:
                    init += ', '
                init += py_ns + ('optional< %s >' % max_params)
            init += ' >()'    
            return init
        
        constructors = [x for x in self.public_members if isinstance(x, Constructor)]
        self.constructors = constructors[:]
        # write the constructor with less parameters to the constructor section
        smaller = None
        for cons in constructors:
            if smaller is None or len(cons.parameters) < len(smaller.parameters):
                smaller = cons
        assert smaller is not None
        self.Add('constructor', init_code(smaller))
        constructors.remove(smaller)
        # write the rest to the inside section, using def()
        for cons in constructors:
            code = '.def(%s)' % init_code(cons) 
            self.Add('inside', code)


    def ExportVariables(self):
        'Export the variables of the class, both static and simple variables'
        vars = [x for x in self.public_members if isinstance(x, Variable)]
        for var in vars:
            if self.info[var.name].exclude: 
                continue
            name = self.info[var.name].rename or var.name
            fullname = var.FullName() 
            if var.type.const:
                def_ = '.def_readonly'
            else:
                def_ = '.def_readwrite'
            code = '%s("%s", &%s)' % (def_, name, fullname)
            self.Add('inside', code)

    
    def OverloadName(self, method):
        'Returns the name of the overloads struct for the given method'
        name = makeid(method.FullName())
        overloads = '_overloads_%i_%i' % (method.minArgs, method.maxArgs)    
        return name + overloads

    
    def GetAddedMethods(self):
        added_methods = self.info.__added__
        result = []
        if added_methods:
            for name, rename in added_methods:
                decl = self.GetDeclaration(name)
                self.info[name].rename = rename
                result.append(decl)
        return result

                
    def ExportMethods(self):
        '''Export all the non-virtual methods of this class, plus any function
        that is to be exported as a method'''
            
        declared = {}
        def DeclareOverloads(m):
            'Declares the macro for the generation of the overloads'
            if (isinstance(m, Method) and m.static) or type(m) == Function:
                func = m.FullName()
                macro = 'BOOST_PYTHON_FUNCTION_OVERLOADS'
            else:
                func = m.name
                macro = 'BOOST_PYTHON_MEMBER_FUNCTION_OVERLOADS' 
            code = '%s(%s, %s, %i, %i)\n' % (macro, self.OverloadName(m), func, m.minArgs, m.maxArgs)
            if code not in declared:
                declared[code] = True
                self.Add('declaration', code)


        def Pointer(m):
            'returns the correct pointer declaration for the method m'
            # check if this method has a wrapper set for him
            wrapper = self.info[m.name].wrapper
            if wrapper:
                return '&' + wrapper.FullName()
            else:
                return m.PointerDeclaration() 

        def IsExportable(m):
            'Returns true if the given method is exportable by this routine'
            ignore = (Constructor, ClassOperator, Destructor)
            return isinstance(m, Function) and not isinstance(m, ignore) and not m.virtual        
        
        methods = [x for x in self.public_members if IsExportable(x)]        
        methods.extend(self.GetAddedMethods())
        
        staticmethods = {}
        
        for method in methods:
            method_info = self.info[method.name]
            
            # skip this method if it was excluded by the user
            if method_info.exclude:
                continue 

            # rename the method if the user requested
            name = method_info.rename or method.name
            
            # warn the user if this method needs a policy and doesn't have one
            method_info.policy = exporterutils.HandlePolicy(method, method_info.policy)
            
            # check for policies
            policy = method_info.policy or ''
            if policy:
                policy = ', %s%s()' % (namespaces.python, policy.Code())
            # check for overloads
            overload = ''
            if method.minArgs != method.maxArgs:
                # add the overloads for this method
                DeclareOverloads(method)
                overload_name = self.OverloadName(method)
                overload = ', %s%s()' % (namespaces.pyste, overload_name)
        
            # build the .def string to export the method
            pointer = Pointer(method)
            code = '.def("%s", %s' % (name, pointer)
            code += policy
            code += overload
            code += ')'
            self.Add('inside', code)
            # static method
            if isinstance(method, Method) and method.static:
                staticmethods[name] = 1
            # add wrapper code if this method has one
            wrapper = method_info.wrapper
            if wrapper and wrapper.code:
                self.Add('declaration', wrapper.code)
        
        # export staticmethod statements
        for name in staticmethods:
            code = '.staticmethod("%s")' % name
            self.Add('inside', code) 


                
    # operators natively supported by boost
    BOOST_SUPPORTED_OPERATORS = '+ - * / % ^ & ! ~ | < > == != <= >= << >> && || += -='\
        '*= /= %= ^= &= |= <<= >>='.split()
    # create a map for faster lookup
    BOOST_SUPPORTED_OPERATORS = dict(zip(BOOST_SUPPORTED_OPERATORS, range(len(BOOST_SUPPORTED_OPERATORS))))

    # a dict of operators that are not directly supported by boost, but can be exposed
    # simply as a function with a special name
    BOOST_RENAME_OPERATORS = {
        '()' : '__call__',
    }

    # converters which have a special name in python
    # it's a map of a regular expression of the converter's result to the
    # appropriate python name
    SPECIAL_CONVERTERS = {
        re.compile(r'(const)?\s*double$') : '__float__',
        re.compile(r'(const)?\s*float$') : '__float__',
        re.compile(r'(const)?\s*int$') : '__int__',
        re.compile(r'(const)?\s*long$') : '__long__',
        re.compile(r'(const)?\s*char\s*\*?$') : '__str__',
        re.compile(r'(const)?.*::basic_string<.*>\s*(\*|\&)?$') : '__str__',
    }
        
    
    def ExportOperators(self):
        'Export all member operators and free operators related to this class'
        
        def GetFreeOperators():
            'Get all the free (global) operators related to this class'
            operators = []
            for decl in self.declarations:
                if isinstance(decl, Operator):
                    # check if one of the params is this class
                    for param in decl.parameters:
                        if param.name == self.class_.FullName():
                            operators.append(decl)
                            break
            return operators

        def GetOperand(param):
            'Returns the operand of this parameter (either "self", or "other<type>")'
            if param.name == self.class_.FullName():
                return namespaces.python + 'self'
            else:
                return namespaces.python + ('other< %s >()' % param.name)


        def HandleSpecialOperator(operator):
            # gatter information about the operator and its parameters
            result_name = operator.result.name                        
            param1_name = ''
            if operator.parameters:
                param1_name = operator.parameters[0].name
                
            # check for str
            ostream = 'basic_ostream'
            is_str = result_name.find(ostream) != -1 and param1_name.find(ostream) != -1
            if is_str:
                namespace = namespaces.python + 'self_ns::'
                self_ = namespaces.python + 'self'
                return '.def(%sstr(%s))' % (namespace, self_)

            # is not a special operator
            return None
                

        
        frees = GetFreeOperators()
        members = [x for x in self.public_members if type(x) == ClassOperator]
        all_operators = frees + members
        operators = [x for x in all_operators if not self.info['operator'][x.name].exclude]
        
        for operator in operators:
            # gatter information about the operator, for use later
            wrapper = self.info['operator'][operator.name].wrapper
            if wrapper:
                pointer = '&' + wrapper.FullName()
                if wrapper.code:
                    self.Add('declaration', wrapper.code)
            else:
                pointer = operator.PointerDeclaration()                 
            rename = self.info['operator'][operator.name].rename

            # check if this operator will be exported as a method
            export_as_method = wrapper or rename or operator.name in self.BOOST_RENAME_OPERATORS
            
            # check if this operator has a special representation in boost
            special_code = HandleSpecialOperator(operator)
            has_special_representation = special_code is not None
            
            if export_as_method:
                # export this operator as a normal method, renaming or using the given wrapper
                if not rename:
                    if wrapper:
                        rename = wrapper.name
                    else:
                        rename = self.BOOST_RENAME_OPERATORS[operator.name]
                policy = ''
                policy_obj = self.info['operator'][operator.name].policy
                if policy_obj:
                    policy = ', %s()' % policy_obj.Code() 
                self.Add('inside', '.def("%s", %s%s)' % (rename, pointer, policy))
            
            elif has_special_representation:
                self.Add('inside', special_code)
                
            elif operator.name in self.BOOST_SUPPORTED_OPERATORS:
                # export this operator using boost's facilities
                op = operator
                is_unary = isinstance(op, Operator) and len(op.parameters) == 1 or\
                           isinstance(op, ClassOperator) and len(op.parameters) == 0
                if is_unary:
                    self.Add('inside', '.def( %s%sself )' % \
                        (operator.name, namespaces.python))
                else:
                    # binary operator
                    if len(operator.parameters) == 2:
                        left_operand = GetOperand(operator.parameters[0])
                        right_operand = GetOperand(operator.parameters[1])
                    else:
                        left_operand = namespaces.python + 'self'
                        right_operand = GetOperand(operator.parameters[0])
                    self.Add('inside', '.def( %s %s %s )' % \
                        (left_operand, operator.name, right_operand))

        # export the converters.
        # export them as simple functions with a pre-determined name

        converters = [x for x in self.public_members if type(x) == ConverterOperator]
                
        def ConverterMethodName(converter):
            result_fullname = converter.result.FullName()
            result_name = converter.result.name
            for regex, method_name in self.SPECIAL_CONVERTERS.items():
                if regex.match(result_fullname):
                    return method_name
            else:
                # extract the last name from the full name
                result_name = makeid(result_name)
                return 'to_' + result_name
            
        for converter in converters:
            info = self.info['operator'][converter.result.FullName()]
            # check if this operator should be excluded
            if info.exclude:
                continue
            
            special_code = HandleSpecialOperator(converter)
            if info.rename or not special_code:
                # export as method
                name = info.rename or ConverterMethodName(converter)
                pointer = converter.PointerDeclaration()
                policy_code = ''
                if info.policy:
                    policy_code = ', %s()' % info.policy.Code()
                self.Add('inside', '.def("%s", %s%s)' % (name, pointer, policy_code))
                    
            elif special_code:
                self.Add('inside', special_code)



    def ExportNestedValueTypes(self, exported_names):
        nested_classes = [x for x in self.public_members if isinstance(x, NestedClass)]
        for nested_class in nested_classes:
            nested_info = self.info[nested_class.name]
            nested_info.include = self.info.include
            nested_info.name = nested_class.FullName()
            exporter = ValueTypeExporter(nested_info)
            exporter.SetDeclarations(self.declarations)
            codeunit = SingleCodeUnit(None, None)
            exporter.Export(codeunit, exported_names)
            self.nested_codeunits.append(codeunit)


    def ExportNestedEnums(self, exported_names):
        nested_enums = [x for x in self.public_members if isinstance(x, ClassEnumeration)]
        for enum in nested_enums:
            enum_info = self.info[enum.name]
            enum_info.include = self.info.include
            enum_info.name = enum.FullName()
            exporter = EnumExporter(enum_info)
            exporter.SetDeclarations(self.declarations)
            codeunit = SingleCodeUnit(None, None)
            exporter.Export(codeunit, exported_names)
            self.nested_codeunits.append(codeunit)


    def ExportSmartPointer(self):
        smart_ptr = self.info.smart_ptr
        if smart_ptr:
            class_name = self.class_.FullName()
            smart_ptr = smart_ptr % class_name
            self.Add('scope', '%sregister_ptr_to_python< %s >();' % (namespaces.python, smart_ptr))
            

    def ExportOpaquePointerPolicies(self):
        # check all methods for 'return_opaque_pointer' policies
        methods = [x for x in self.public_members if isinstance(x, Method)]
        for method in methods:
            return_opaque_policy = return_value_policy(return_opaque_pointer)
            if self.info[method.name].policy == return_opaque_policy:
                macro = exporterutils.EspecializeTypeID(method.result.name) 
                if macro:
                    self.Add('declaration-outside', macro)


#==============================================================================
# Virtual Wrapper utils
#==============================================================================

def _ParamsInfo(m, count=None):
    if count is None:
        count = len(m.parameters)
    param_names = ['p%i' % i for i in range(count)]
    param_types = [x.FullName() for x in m.parameters[:count]]
    params = ['%s %s' % (t, n) for t, n in zip(param_types, param_names)]
    #for i, p in enumerate(m.parameters[:count]):
    #    if p.default is not None:
    #        #params[i] += '=%s' % p.default
    #        params[i] += '=%s' % (p.name + '()')
    params = ', '.join(params) 
    return params, param_names, param_types
