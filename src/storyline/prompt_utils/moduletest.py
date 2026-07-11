import argparse
import importlib

def load_and_execute_function(module_name, function_name, *args, **kwargs):
    """
    Dynamically loads and executes a function from a specified module.

    Args:
        module_name (str): The name of the Python module.
        function_name (str): The name of the function within the module.
        *args: Positional arguments to pass to the loaded function.
        **kwargs: Keyword arguments to pass to the loaded function.

    Returns:
        The return value of the executed function, or None if an error occurs.
    """
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
        return function(*args, **kwargs)
    except ImportError:
        print(f"Error: Module '{module_name}' not found.")
        return None
    except AttributeError:
        print(f"Error: Function '{function_name}' not found in module '{module_name}'.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Dynamically load and execute functions.")
    parser.add_argument("module", help="The module containing the function.")
    parser.add_argument("function", default="do_the_thing", help="The function to execute.")
    parser.add_argument("args", nargs="*", help="Arguments to pass to the function.")

    args = parser.parse_args()

    # Convert string arguments to appropriate types if needed.
    # For example, if you expect integers, you might do:
    # converted_args = [int(arg) if arg.isdigit() else arg for arg in args.args]

    result = load_and_execute_function(args.module, args.function, *args.args)

    if result is not None:
        print("Result:", result)

if __name__ == "__main__":
    main()

# Example usage (assuming you have modules like 'module1.py' and 'module2.py'):
#
# module1.py:
# def my_function(x, y):
#     return x + y
#
# module2.py:
# def another_function(name):
#     return f"Hello, {name}!"
#
# To run:
# python your_script.py module1 my_function 5 10
# python your_script.py module2 another_function Alice