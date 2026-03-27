package workflow

import "fmt"

// DataKind identifies one typed expression-context value shape.
type DataKind string

const (
	// DataKindNull is a null value.
	DataKindNull DataKind = "null"
	// DataKindString is a string value.
	DataKindString DataKind = "string"
	// DataKindBoolean is a boolean value.
	DataKindBoolean DataKind = "boolean"
	// DataKindNumber is a numeric value.
	DataKindNumber DataKind = "number"
	// DataKindArray is an array value.
	DataKindArray DataKind = "array"
	// DataKindObject is an object value.
	DataKindObject DataKind = "object"
)

// Data is ghawfr's typed expression-context value tree.
type Data struct {
	Kind    DataKind
	String  string
	Boolean bool
	Number  float64
	Array   []Data
	Object  map[string]Data
}

// NullData returns a null value.
func NullData() Data { return Data{Kind: DataKindNull} }

// StringData returns a string value.
func StringData(value string) Data { return Data{Kind: DataKindString, String: value} }

// BooleanData returns a boolean value.
func BooleanData(value bool) Data { return Data{Kind: DataKindBoolean, Boolean: value} }

// NumberData returns a numeric value.
func NumberData(value float64) Data { return Data{Kind: DataKindNumber, Number: value} }

// ArrayData returns an array value.
func ArrayData(values ...Data) Data {
	return Data{Kind: DataKindArray, Array: append([]Data(nil), values...)}
}

// ObjectData returns an object value.
func ObjectData(values map[string]Data) Data {
	clone := make(map[string]Data, len(values))
	for key, value := range values {
		clone[key] = value.Clone()
	}
	return Data{Kind: DataKindObject, Object: clone}
}

// Clone returns a deep copy of the data value.
func (d Data) Clone() Data {
	clone := Data{Kind: d.Kind, String: d.String, Boolean: d.Boolean, Number: d.Number}
	if len(d.Array) > 0 {
		clone.Array = make([]Data, 0, len(d.Array))
		for _, value := range d.Array {
			clone.Array = append(clone.Array, value.Clone())
		}
	}
	if len(d.Object) > 0 {
		clone.Object = make(map[string]Data, len(d.Object))
		for key, value := range d.Object {
			clone.Object[key] = value.Clone()
		}
	}
	return clone
}

// Any converts the typed value into the generic shape required by third-party evaluators.
func (d Data) Any() any {
	switch d.Kind {
	case DataKindNull:
		return nil
	case DataKindString:
		return d.String
	case DataKindBoolean:
		return d.Boolean
	case DataKindNumber:
		return d.Number
	case DataKindArray:
		values := make([]any, 0, len(d.Array))
		for _, value := range d.Array {
			values = append(values, value.Any())
		}
		return values
	case DataKindObject:
		values := make(map[string]any, len(d.Object))
		for key, value := range d.Object {
			values[key] = value.Any()
		}
		return values
	default:
		return fmt.Sprintf("%v", d.String)
	}
}

// DataFromAny converts one generic value into the typed tree.
func DataFromAny(value any) Data {
	switch value := value.(type) {
	case nil:
		return NullData()
	case string:
		return StringData(value)
	case bool:
		return BooleanData(value)
	case int:
		return NumberData(float64(value))
	case int64:
		return NumberData(float64(value))
	case float64:
		return NumberData(value)
	case []any:
		items := make([]Data, 0, len(value))
		for _, item := range value {
			items = append(items, DataFromAny(item))
		}
		return Data{Kind: DataKindArray, Array: items}
	case map[string]any:
		object := make(map[string]Data, len(value))
		for key, item := range value {
			object[key] = DataFromAny(item)
		}
		return Data{Kind: DataKindObject, Object: object}
	default:
		return StringData(fmt.Sprintf("%v", value))
	}
}
